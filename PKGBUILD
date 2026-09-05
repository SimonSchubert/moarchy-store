# Maintainer: Simon Schubert <simon@librem.one>
pkgname=moarchy-store-git
_pkgname=moarchy-store
pkgver=0.1.0
pkgrel=1
pkgdesc="Curated store of Linux phone apps that actually fit a small screen"
arch=('any')
url="https://github.com/SimonSchubert/moarchy-store"
license=('MIT')
# Pure Python, so arch=any. The GUI stack is all runtime, nothing is compiled.
depends=('python' 'python-gobject' 'gtk4' 'libadwaita' 'polkit' 'pacman')
makedepends=('git')
provides=("$_pkgname")
conflicts=("$_pkgname")
source=("$_pkgname::git+$url.git")
sha256sums=('SKIP')

pkgver() {
  cd "$srcdir/$_pkgname"
  git describe --long --tags 2>/dev/null | sed 's/^v//;s/\([^-]*-g\)/r\1/;s/-/./g' ||
    printf "0.1.0.r%s.%s" "$(git rev-list --count HEAD)" "$(git rev-parse --short HEAD)"
}

package() {
  cd "$srcdir/$_pkgname"

  local site
  site="$(python3 -c 'import site; print(site.getsitepackages()[0])')"
  install -dm755 "$pkgdir$site/moarchy_store"
  install -Dm644 moarchy_store/*.py -t "$pkgdir$site/moarchy_store/"

  install -Dm755 bin/moarchy-store "$pkgdir/usr/bin/moarchy-store"

  # The catalogue doubles as the install allowlist, so it must land root-owned
  # and not group- or world-writable. The helper refuses to run otherwise.
  install -Dm644 catalogue.toml "$pkgdir/usr/share/$_pkgname/catalogue.toml"

  # Not in /usr/bin: this is only ever invoked through the polkit action, and
  # putting it on PATH invites someone to call it directly and be confused by
  # the permission error.
  install -Dm755 data/moarchy-store-helper \
    "$pkgdir/usr/lib/$_pkgname/moarchy-store-helper"

  install -Dm644 data/org.moarchy.Store.policy \
    "$pkgdir/usr/share/polkit-1/actions/org.moarchy.Store.policy"
  install -Dm644 data/org.moarchy.Store.desktop \
    "$pkgdir/usr/share/applications/org.moarchy.Store.desktop"

  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
  install -Dm644 README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
}
