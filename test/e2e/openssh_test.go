//go:build darwin || linux

package e2e

import (
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	xproxy "golang.org/x/net/proxy"
)

// One end-to-end scenario covers startup, traffic, reconnection, and cleanup.
//
//gocyclo:ignore
func TestOpenSSHSocks(t *testing.T) {
	// macOS's default temp path can exceed the Unix socket path limit.
	t.Setenv("TMPDIR", "/tmp")
	if _, err := os.Stat("/usr/bin/ssh"); err != nil {
		t.Skip("requires /usr/bin/ssh")
	}
	dir := t.TempDir()
	write := func(name, data string) string {
		t.Helper()
		path := filepath.Join(dir, name)
		if err := os.WriteFile(path, []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
		return path
	}
	key, err := os.ReadFile("../testdata/keys/client")
	if err != nil {
		t.Fatal(err)
	}
	identity := write("key with spaces", string(key))
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := listener.Addr().String()
	defer listener.Close()
	cfg := defaultConfig
	cfg.sshConfig = write("ssh_config", fmt.Sprintf(`Host target
  ProxyJump jump
Host target jump
  HostName 127.0.0.1
  Port 58391
  User test
  IdentityFile %q
  IdentityAgent none
  AddKeysToAgent no
  IdentitiesOnly yes
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null
  ControlMaster no
  ControlPersist yes
`, identity))
	cfg.boringConfig = write("config.toml", fmt.Sprintf(`[[tunnels]]
name = "openssh"
backend = "openssh"
host = "target"
mode = "socks"
local = %q
keep_alive = 1

[[tunnels]]
name = "unsupported"
backend = "openssh"
host = "target"
mode = "local"
local = "1234"
remote = "localhost:1234"

[[tunnels]]
name = "typo"
backend = "opnessh"
host = "target"
mode = "socks"
local = "1234"
`, address))
	env, cancel, err := makeEnvWithDaemon(cfg, t)
	if err != nil {
		t.Fatal(err)
	}
	defer cancel()
	command := func(want int, args ...string) string {
		t.Helper()
		code, out, err := cliCommand(env, args...)
		if err != nil || code != want {
			t.Fatalf("%v: code=%d err=%v output=%s", args, code, err, out)
		}
		return out
	}
	command(1, "open", "unsupported")
	command(1, "open", "typo")
	// A pre-existing listener must not count as a successful tunnel start.
	if out := command(1, "open", "openssh"); !strings.Contains(out, "forward") {
		t.Fatalf("missing OpenSSH forwarding error: %s", out)
	}
	listener.Close()
	command(0, "open", "openssh")

	httpServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, "through OpenSSH")
	}))
	defer httpServer.Close()
	dialer, err := xproxy.SOCKS5("tcp", address, nil, &net.Dialer{Timeout: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	transport := &http.Transport{Dial: dialer.Dial, DisableKeepAlives: true}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: time.Second}
	request := func() error {
		resp, err := client.Get(httpServer.URL)
		if err != nil {
			return err
		}
		defer resp.Body.Close()
		body, err := io.ReadAll(resp.Body)
		if err != nil || string(body) != "through OpenSSH" {
			return fmt.Errorf("body=%q err=%v", body, err)
		}
		return nil
	}
	if err := request(); err != nil {
		t.Fatal(err)
	}
	// Reconnection must rebuild the full ProxyJump chain without an agent.
	server.closeAll()
	deadline := time.Now().Add(5 * time.Second)
	for {
		if err := request(); err == nil {
			break
		} else if time.Now().After(deadline) {
			t.Fatalf("did not reconnect: %v", err)
		}
		time.Sleep(50 * time.Millisecond)
	}
	command(0, "close", "openssh")
	if conn, err := net.DialTimeout("tcp", address, time.Second); err == nil {
		conn.Close()
		t.Fatal("SOCKS listener survived close")
	}
	// Both destination and jump connections must disappear (no orphan child).
	deadline = time.Now().Add(3 * time.Second)
	for {
		server.mu.Lock()
		remaining := len(server.conns)
		server.mu.Unlock()
		if remaining == 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("%d SSH connections survived close", remaining)
		}
		time.Sleep(20 * time.Millisecond)
	}
}
