//go:build !darwin && !linux

package tunnel

import "fmt"

func (t *Tunnel) openSSH() error {
	return fmt.Errorf("openssh backend requires macOS or Linux")
}
