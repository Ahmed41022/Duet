# Use HTTPS from a phone

Duet serves HTTP by default. Enable HTTPS in the desktop app or run:

```powershell
duet serve --https
```

The PC issues a short-lived server certificate for its selected private
IPv4 address, signed by a self-signed root certificate unique to that PC user.
The phone's browser validates the server certificate against the root you
install and checks that its IP matches the HTTPS address. Duet leaves the PC's
trust settings alone.

## Verify the public certificate

1. On the PC, start HTTPS sharing and find the path to `duet-root.cer` and
   the separate **Root certificate SHA-256** field. The path is inside Duet's
   per-user data directory. Copy only `duet-root.cer` to your phone via a
   trusted channel, such as USB or your own trusted file or mail account.
2. Compare the SHA-256 fingerprint of the received certificate with the one
   displayed on the PC, using the phone's certificate details if available.
   The file contains the raw DER certificate, so its SHA-256 file hash is
   the same as its certificate fingerprint. For example, to check a copy in a
   trusted location on Windows, run this from its directory:

   ```powershell
   Get-FileHash -Algorithm SHA256 -LiteralPath .\duet-root.cer
   ```

   Compare all 64 hexadecimal characters, ignoring case and colons. If you
   cannot verify the copy on the phone, use a direct trusted transfer from the
   PC and confirm the hash of the exact source file before copying it.
3. Use the QR on the PC or open the displayed IP and port in your browser.
   The QR includes the pairing token and the same root fingerprint in the
   URL fragment. The phone browser can only validate TLS through its trust
   settings. The fragment is a reference for comparison and cannot configure
   browser trust. Install the certificate first if the browser shows a warning,
   and only pair once the browser connects normally over HTTPS.

The fingerprint identifies the **root certificate in the .cer file**, so it
stays the same when Duet starts again or the PC uses a different private IP.
A changed IP or port can require a new pairing and a new home-screen install.
Keep `root-private.pem` private and leave it in Duet's data directory. Installing
a root CA grants device-level trust to certificates it can sign. The certificate
name alone does not identify your PC. Only trust the root you verified from your
own PC, and remove it from the phone when finished using HTTPS sharing.

## Android

Save the verified `duet-root.cer` locally. In Settings, find **Encryption &
credentials**, then choose **Install a certificate** and **CA certificate**.
Some devices call this **Install from device storage**. Select the verified
file and confirm with your device lock. Then open the HTTPS URL in Chrome
and pair as usual. The settings path varies by manufacturer. To remove trust,
locate the Duet root under your user credentials and remove that entry.
[Google's Android CA installation steps](https://support.google.com/device-usage-study-help/answer/15713321?co=GENIE.Platform%3DAndroid&hl=en)

## iPhone and iPad

Open the verified public `.cer` attachment from a trusted source in Mail.
In Settings, select **Profile Downloaded** and install it. Then open
**Settings > General > About > Certificate Trust Settings**, and enable full
trust for **Duet Local Root**. Open the HTTPS URL in Safari and pair as usual.
To remove trust, remove the Duet certificate profile under **Settings >
General > VPN & Device Management**.
[Receiving a certificate](https://support.apple.com/en-us/guide/deployment/depcdc9a6a3f/web)
[Installing a profile](https://support.apple.com/en-us/102400)
[Enabling TLS trust](https://support.apple.com/en-us/102390)

## Manual phone checklist

- With no Duet root installed, open the HTTPS URL and confirm the browser
  stops at a certificate warning. Leave the warning screen without pairing.
- Transfer the public file, compare its SHA-256 with the PC, and install it.
  Open the same URL again and confirm the page loads with a valid HTTPS
  connection and no warning.
- Scan the QR and pair. Then restart Duet and test PIN pairing as well.
  Send text and a file in both directions over HTTPS.
- Restart Duet on a different private IPv4 address if available. Confirm
  that the root fingerprint is unchanged and that the new HTTPS address
  connects without another certificate install.
- Remove the Duet certificate from the phone, close the browser connection
  and confirm a new visit to the HTTPS URL produces a certificate warning.
- Start standard HTTP sharing and confirm PIN pairing works using the
  displayed HTTP URL.
