# Homelab runbook

The NAS lives at 192.168.1.20 on the home network and is reachable over SSH on the default port.

## Backups
Nightly backups run at 02:30 to the external USB drive. Verify the last backup log every Monday.

## Maintenance
Run a ZFS scrub on the first Sunday of every month. The UPS keeps the NAS running for about
25 minutes during a power cut, which is enough for a clean shutdown.
