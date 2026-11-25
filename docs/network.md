# Connect your phone to Duet

Put the phone and PC on the same trusted Wi-Fi network, or connect the phone to a hotspot started by the PC. Start Duet and scan the QR code or open the displayed address in the phone browser. If you enter the address manually, use the pairing PIN shown on the PC.

Duet displays the selected interface and local IPv4 address. It prefers a hotspot, Wi-Fi, or Ethernet interface over common VPN and virtualization adapters. Windows can report a Public or Private network profile. On Linux, or when Windows cannot report a profile, Duet shows that the network is unverified. A private IPv4 address alone does not tell you who else can access that network.

If the QR code shows the wrong interface, select the correct address under **Settings > Network address** or run `duet serve --bind ADDRESS`. You must select a private IPv4 address currently assigned to the PC. If the address disappears while Duet is running, start Duet again to get a new address and QR code. Duet also stops after 15 minutes of inactivity by default. For the CLI, pass `--config settings.toml` and set `idle_timeout_seconds` to the number of seconds you want.

## Windows Mobile Hotspot

1. On the PC, open **Settings > Network & internet > Mobile hotspot**. Choose Wi-Fi under **Share over**, set a password, and turn on Mobile hotspot.
2. On the phone, join the hotspot by name using that password.
3. Start Duet after turning on the hotspot. On a typical Windows setup the hotspot address is `192.168.137.1`. If needed, check the hotspot adapter's IPv4 address with `ipconfig` and select that address in Duet.
4. Scan the QR code from the phone and pair.

[Microsoft Mobile Hotspot instructions](https://support.microsoft.com/en-gb/windows/experience/connectivity-networking/use-your-windows-device-as-a-mobile-hotspot?nochrome=true)

When Windows Firewall prompts for access on a trusted private network, allow access for **Private networks** only. If you already dismissed the prompt, check **Windows Security > Firewall & network protection > Allow an app through firewall**. Leave the firewall on and Public access disabled. Windows may classify the hotspot as Public or leave the profile unavailable. If a private-only rule does not permit the connection, use a trusted Wi-Fi network that Windows recognizes as Private.

[Windows Firewall app permissions](https://support.microsoft.com/en-gb/windows/security/firewall/risks-of-allowing-apps-through-windows-firewall?nochrome=true)

## Linux hotspot

1. If your distribution uses NetworkManager, open Wi-Fi settings and choose **Turn On Wi-Fi Hotspot** or the equivalent option. Set a strong password. Some Wi-Fi adapters can only be in one mode at a time, so you may need Ethernet for the PC's upstream connection.
2. On the phone, join the hotspot. Start Duet after enabling the hotspot so it can find the new address.
3. Scan the QR code and pair. If Duet chose another interface, use the hotspot interface's IPv4 address from `ip -4 addr`.

[GNOME hotspot instructions](https://help.gnome.org/gnome-help/net-wireless-adhoc.html)

If your Linux firewall blocks the phone, allow inbound TCP to Duet's configured port (default `8765`) only on the trusted LAN or hotspot interface and from its local subnet. Use your distribution's firewall settings to make this scoped rule. Keep public interfaces closed.

## If the phone cannot connect

- Check that the phone joined the intended network and that Duet still shows the same IPv4 address and port.
- Check whether a guest network isolates devices. Use a trusted Wi-Fi network or your PC's hotspot when peer devices cannot connect.
- Check the private-network firewall permission. A scan of the QR code can succeed even when the browser cannot reach Duet.

## Device verification checklist

Physical phone testing is pending.

- Pair from a phone on trusted Wi-Fi and send a file in each direction.
- Repeat on Windows Mobile Hotspot and on a Linux hotspot with a supported Wi-Fi adapter.
- Repeat address selection with a VPN or Docker interface up.
- Turn off the hotspot while Duet is running, confirm it stops, then restart it on the replacement network and scan the new QR code.
- Confirm an open phone page eventually times out after inactivity and a transfer in progress can finish even if it takes longer than the idle timeout.
