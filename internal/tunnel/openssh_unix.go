//go:build darwin || linux

package tunnel

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/alebeck/boring/internal/log"
	"github.com/alebeck/boring/internal/paths"
)

func (t *Tunnel) openSSHArgs(socket string) ([]string, error) {
	// ponytail: only TCP SOCKS is needed today; add other forwarding modes when used.
	if t.Mode != Socks {
		return nil, fmt.Errorf("openssh backend supports only mode = socks")
	}
	if t.Host == "" || strings.HasPrefix(t.Host, "-") {
		return nil, fmt.Errorf("invalid SSH host %q", t.Host)
	}
	addr, err := parseAddr(t.LocalAddress.String(), true)
	if err != nil || addr.net != "tcp" {
		return nil, fmt.Errorf("openssh SOCKS requires a TCP local address")
	}
	args := []string{"-N", "-T", "-S", socket,
		"-o", "ControlMaster=yes", "-o", "ControlPersist=no",
		"-o", "ForkAfterAuthentication=no", "-o", "ExitOnForwardFailure=yes",
		"-o", "PermitLocalCommand=no", "-o", "LogLevel=ERROR",
		"-D", addr.addr}
	if config := os.Getenv("BORING_SSH_CONFIG"); config != "" {
		args = append(args, "-F", paths.ReplaceTilde(config))
	}
	if t.User != "" {
		args = append(args, "-l", t.User)
	}
	if t.Port != "" {
		args = append(args, "-p", t.Port.String())
	}
	if t.IdentityFile != "" {
		args = append(args, "-i", paths.ReplaceTilde(t.IdentityFile))
	}
	if t.KeepAlive != nil {
		args = append(args, "-o", "ServerAliveInterval="+strconv.Itoa(*t.KeepAlive))
	}
	return append(args, "--", t.Host), nil
}

func (t *Tunnel) openSSH() error {
	// A private, short control socket lets OpenSSH report readiness after auth and
	// forwarding setup. Probing the SOCKS port could mistake another process for us.
	dir, err := os.MkdirTemp("", "boring-ssh-")
	if err != nil {
		return err
	}
	socket := filepath.Join(dir, "ctl")
	args, err := t.openSSHArgs(socket)
	if err != nil {
		os.RemoveAll(dir)
		return err
	}
	sshBinary := "ssh"
	if runtime.GOOS == "darwin" {
		// Apple's SSH provides Secure Enclave support; Linux uses PATH (also on NixOS).
		sshBinary = "/usr/bin/ssh"
	}
	cmd := exec.Command(sshBinary, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	cmd.WaitDelay = time.Second
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		os.RemoveAll(dir)
		return fmt.Errorf("start OpenSSH: %w", err)
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	// Kill the whole session's process group, including ProxyJump children.
	kill := func() { _ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }
	failure := func(err error) error {
		return fmt.Errorf("OpenSSH: %v: %s", err, strings.TrimSpace(stderr.String()))
	}
	timeout := time.NewTimer(time.Minute)
	defer timeout.Stop()
	poll := time.NewTicker(50 * time.Millisecond)
	defer poll.Stop()
	for {
		select {
		case err := <-done:
			kill()
			os.RemoveAll(dir)
			return failure(err)
		case <-timeout.C:
			kill()
			<-done
			os.RemoveAll(dir)
			return failure(fmt.Errorf("startup timed out"))
		case <-t.stop:
			kill()
			<-done
			os.RemoveAll(dir)
			return fmt.Errorf("OpenSSH startup cancelled")
		case <-poll.C:
			if _, err := os.Stat(socket); err != nil {
				continue
			}
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			check := exec.CommandContext(ctx, cmd.Path, "-F", "/dev/null", "-S", socket, "-O", "check", "unused")
			err := check.Run()
			cancel()
			if err != nil {
				continue
			}
			if t.stop == nil {
				t.stop = make(chan struct{})
				t.Closed = make(chan struct{})
			}
			t.Status = Open
			t.LastConn = time.Now()
			go t.runOpenSSH(done, kill, func() { os.RemoveAll(dir) }, failure)
			return nil
		}
	}
}

func (t *Tunnel) runOpenSSH(done <-chan error, kill, cleanup func(), failure func(error) error) {
	stopped := false
	select {
	case <-t.stop:
		stopped = true
		kill()
		<-done
	case err := <-done:
		kill()
		log.Errorf("%s: %v", t.Name, failure(err))
	}
	cleanup()
	if !stopped {
		if err := t.reconnectLoop(); err == nil {
			return
		} else {
			log.Errorf("%s: %v", t.Name, err)
		}
	}
	t.Status = Closed
	close(t.Closed)
}
