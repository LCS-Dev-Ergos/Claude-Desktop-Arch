# Arch Linux packaging of Anthropic's official Linux application.
# Upstream version and asset hash are updated by scripts/check-update.py.
pkgname=claude-desktop-sandboxed
pkgver=2.37337.1
pkgrel=3
pkgdesc='Claude Desktop with required renderer sandboxing and a restricted launcher'
arch=('x86_64')
url='https://claude.com/download'
license=('LicenseRef-Proprietary' 'MIT')
provides=('claude-desktop')
conflicts=('claude-desktop' 'claude-desktop-bin' 'claude-desktop-local')
options=('!strip' '!debug')

# Direct ELF dependencies plus desktop integration used through dlopen/D-Bus.
depends=(
  'alsa-lib' 'at-spi2-core' 'cairo' 'dbus' 'expat' 'glib2' 'glibc'
  'gtk3' 'hicolor-icon-theme' 'libcap-ng' 'libcups' 'libdrm' 'libgcc'
  'libnotify' 'libpipewire' 'libseccomp' 'libsecret' 'libstdc++'
  'libx11' 'libxcb' 'libxcomposite' 'libxdamage' 'libxext' 'libxfixes'
  'libxkbcommon' 'libxrandr' 'libxtst' 'mesa' 'nspr' 'nss' 'pango'
  'systemd-libs' 'util-linux-libs' 'xdg-desktop-portal' 'xdg-utils'
)
makedepends=('python')
optdepends=(
  'bubblewrap: Claude Code command sandboxing'
  'socat: Claude Code sandbox networking'
  'openssh: SSH client for remote connections'
  'qemu-system-x86: Cowork virtual machine'
  'edk2-ovmf: Cowork virtual machine firmware'
  'virtiofsd: Cowork host/guest filesystem sharing'
  'gnome-keyring: Secret Service credential storage'
  'kwallet: KDE credential storage'
  'libayatana-appindicator: system tray integration'
  'xdg-desktop-portal-kde: portal backend for Plasma'
  'xdg-desktop-portal-gtk: portal backend for GTK desktops'
)

_baseurl='https://downloads.claude.ai/claude-desktop/apt/stable/pool/main/c/claude-desktop/'
_upstream_sha256='41745b0914f0ff51631a1ff1bb403b836bf5c995845cca6742dd39bca3a2da14'
source=(
  'claude-desktop.sh'
  'upstream.py'
  'package-assets.py'
  'upstream.json'
  'LICENSE.md'
  "${_baseurl}claude-desktop_${pkgver}_amd64.deb"
)
# Extraction is performed explicitly after checksum validation.
noextract=("claude-desktop_${pkgver}_amd64.deb")
sha256sums=(
  '2fe62e4be79686a99e33336c35fb78452ad06aad2e811b121f4d65cb797d989d'
  'f9f7e2b630fb42dcd84f34f34e7612cf3a4cb011af47cc8b7af24e0cb00772e1'
  '50cf5b14afde9d6cdf8d3bce2d4211df037df5fff80ba6433c2be0f3fd461fee'
  '615f898ca6c782e16dd7e8bfd7af29a8a8482db36b4a04f66164eee4f90cf897'
  'b13bc406b3ef146d0046a9069d96ae7dcdfb8b9d6c067fb1d80a1d7d02a81023'
  "$_upstream_sha256"
)

# Validate the reviewed payload and stage only application/integration assets.
prepare() {
  rm -rf -- "$srcdir/claude-staging"
  python "$srcdir/package-assets.py" \
    "$srcdir/claude-desktop_${pkgver}_amd64.deb" \
    "$srcdir/upstream.json" "$srcdir/claude-staging"
}

# Install the staged runtime and resources without Debian hooks or setuid files.
package() {
  local staging="$srcdir/claude-staging"
  install -d "$pkgdir/usr/lib" "$pkgdir/usr/share"
  cp -a --no-preserve=ownership "$staging/usr/lib/claude-desktop" "$pkgdir/usr/lib/"
  cp -a --no-preserve=ownership "$staging/usr/share/applications" "$pkgdir/usr/share/"
  cp -a --no-preserve=ownership "$staging/usr/share/icons" "$pkgdir/usr/share/"
  install -Dm755 "$srcdir/claude-desktop.sh" "$pkgdir/usr/bin/claude-desktop"
  install -Dm644 "$staging/usr/share/doc/claude-desktop/copyright" \
    "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
  install -Dm644 "$srcdir/LICENSE.md" \
    "$pkgdir/usr/share/licenses/$pkgname/Packaging-MIT"

  # Cowork searches Debian paths; dependencies remain optional until it is used.
  install -d "$pkgdir/usr/libexec" "$pkgdir/usr/share/edk2"
  ln -s ../lib/virtiofsd "$pkgdir/usr/libexec/virtiofsd"
  ln -s x64/OVMF_CODE.4m.fd "$pkgdir/usr/share/edk2/OVMF_CODE_4M.fd"
  ln -s x64/OVMF_VARS.4m.fd "$pkgdir/usr/share/edk2/OVMF_VARS_4M.fd"
}
