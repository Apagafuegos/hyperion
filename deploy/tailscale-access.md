# Private Hyperion access

The deployed console is available at
`https://vnic-vps.tail5c9091.ts.net:8787/atlas` from the owner's Tailscale devices.
It uses the owner's Tailscale identity without an Authentik login prompt.

The persistent Tailscale Serve route is:

```sh
sudo tailscale serve --bg --yes --https=8787 http://127.0.0.1:8788
```

Caddy's private listener binds only `127.0.0.1:8788`. It checks the
`Tailscale-User-Login` header against the owner's login before proxying to
Hyperion at `127.0.0.1:8787`. Tailscale Serve supplies this header and strips
client-supplied identity headers. The private listener supplies Hyperion's
trusted username, administrator group, and existing proxy credential from
`HYPERION_PROXY_SECRET` in Caddy's environment. Requests with missing or other
identities return 403.

The private listener is configured in `/etc/caddy/Caddyfile` under
`Hyperion private Tailscale access`. Its site address is `http://:8788` with
`bind 127.0.0.1`, so it accepts the forwarded Tailscale hostname while listening
only on loopback. The public domain retains Authentik authentication.

Inspect or disable this route without changing the other Tailscale endpoints:

```sh
tailscale serve status
sudo tailscale serve --https=8787 off
```

See [Tailscale Serve identity headers](https://tailscale.com/docs/features/tailscale-serve#identity-headers).
