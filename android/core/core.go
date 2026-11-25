// Package core routes each new flow once. The UID lookup is supplied by the active Android VpnService.
package core

import (
	"context"
	"errors"
	"net"
	"net/url"
	"strconv"
	"strings"
	"sync"

	"github.com/xjasonlyu/tun2socks/v2/core"
	"github.com/xjasonlyu/tun2socks/v2/core/device"
	"github.com/xjasonlyu/tun2socks/v2/core/device/fdbased"
	"github.com/xjasonlyu/tun2socks/v2/log"
	M "github.com/xjasonlyu/tun2socks/v2/metadata"
	"github.com/xjasonlyu/tun2socks/v2/proxy"
	"github.com/xjasonlyu/tun2socks/v2/proxy/direct"
	"github.com/xjasonlyu/tun2socks/v2/proxy/socks5"
	"github.com/xjasonlyu/tun2socks/v2/tunnel"
	"github.com/xjasonlyu/tun2socks/v2/tunnel/statistic"
	"golang.org/x/sys/unix"
	"gvisor.dev/gvisor/pkg/tcpip/stack"
)

type Resolver interface {
	Mode(protocol int, source string, sourcePort int, destination string, destinationPort int) string
}

type router struct {
	owner                     Resolver
	mu                        sync.RWMutex
	direct, forced, automatic proxy.Proxy
}

func endpoint(raw string) (proxy.Proxy, error) {
	if raw == "" {
		return nil, nil
	}
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "socks5" || u.Hostname() == "" || u.Port() == "" || u.Path != "" || u.RawQuery != "" || u.Fragment != "" {
		return nil, errors.New("请输入 socks5://主机:端口")
	}
	n, err := strconv.Atoi(u.Port())
	if err != nil || n < 1 || n > 65535 || strings.ContainsAny(u.Hostname(), " /") {
		return nil, errors.New("SOCKS5 地址无效")
	}
	password, _ := u.User.Password()
	return socks5.New(net.JoinHostPort(u.Hostname(), u.Port()), u.User.Username(), password)
}

func ValidateEndpoint(raw string) error { _, err := endpoint(raw); return err }
func (r *router) choose(m *M.Metadata, protocol int) (proxy.Proxy, error) {
	mode := r.owner.Mode(protocol, m.SrcIP.String(), int(m.SrcPort), m.DstIP.String(), int(m.DstPort))
	r.mu.RLock()
	defer r.mu.RUnlock()
	switch mode {
	case "proxy":
		if r.forced == nil {
			return nil, errors.New("未设置代理出口")
		}
		return r.forced, nil
	case "auto":
		if r.automatic != nil {
			return r.automatic, nil
		}
	}
	return r.direct, nil
}
func (r *router) DialContext(ctx context.Context, m *M.Metadata) (net.Conn, error) {
	p, err := r.choose(m, 6)
	if err != nil {
		return nil, err
	}
	return p.DialContext(ctx, m)
}
func (r *router) DialUDP(m *M.Metadata) (net.PacketConn, error) {
	p, err := r.choose(m, 17)
	if err != nil {
		return nil, err
	}
	return p.DialUDP(m)
}

type Session struct {
	dev    device.Device
	stack  *stack.Stack
	tunnel *tunnel.Tunnel
	router *router
	once   sync.Once
}

// Start duplicates the descriptor. Java keeps ownership of the original.
func Start(fd int, resolver Resolver, forced, automatic string) (*Session, error) {
	if resolver == nil {
		return nil, errors.New("缺少应用归属查询")
	}
	logger, _ := log.NewLeveled(log.SilentLevel)
	log.SetLogger(logger)
	s := &Session{router: &router{owner: resolver}}
	s.router.direct, _ = direct.New()
	if err := s.Configure(forced, automatic); err != nil {
		return nil, err
	}
	dup, err := unix.Dup(fd)
	if err != nil {
		return nil, err
	}
	s.dev, err = fdbased.Open(strconv.Itoa(dup), 1500, 0)
	if err != nil {
		unix.Close(dup)
		return nil, err
	}
	s.tunnel = tunnel.New(s.router, statistic.DefaultManager)
	s.tunnel.ProcessAsync()
	s.stack, err = core.CreateStack(&core.Config{LinkEndpoint: s.dev, TransportHandler: s.tunnel})
	if err != nil {
		s.dev.Close()
		s.tunnel.Close()
		return nil, err
	}
	return s, nil
}
func (s *Session) Configure(forced, automatic string) error {
	f, err := endpoint(forced)
	if err != nil {
		return err
	}
	a, err := endpoint(automatic)
	if err != nil {
		return err
	}
	s.router.mu.Lock()
	s.router.forced, s.router.automatic = f, a
	s.router.mu.Unlock()
	return nil
}
func (s *Session) Close() {
	s.once.Do(func() { s.dev.Close(); s.stack.Close(); s.stack.Wait(); s.tunnel.Close() })
}
