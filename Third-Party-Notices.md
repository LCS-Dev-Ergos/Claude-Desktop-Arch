# Third-Party Notices

## Original Packaging Material

The MIT license in [LICENSE.md](LICENSE.md) applies to original packaging code, tests, and documentation. It does not change the terms of upstream materials or downloaded application components.

## Claude Desktop Application

Claude Desktop is published by Anthropic. Its executable, Electron runtime, ASAR resources, icons, and bundled components are downloaded from the official upstream repository during package builds; they are not part of this Git repository.

The PKGBUILD records `LicenseRef-Proprietary` for the installed application and `MIT` for the original launcher. It installs upstream's Debian copyright notice under `/usr/share/licenses/claude-desktop-sandboxed/LICENSE` and the packaging MIT notice alongside it as `Packaging-MIT`. Bundled components retain their respective upstream license notices. The MIT license for packaging is not a license for the application assets.

## Public Key And Signed Test Metadata

`keys/anthropic.asc` is Anthropic's upstream public signing key. `tests/fixtures/InRelease` is upstream-signed APT metadata retained as a signature-verification fixture. These are upstream-origin trust/test materials, not original MIT-licensed packaging code. Their identity and signature bytes must be preserved when used for verification.

The pinned primary fingerprint is `31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE`. Verify key changes against [Anthropic's installation documentation](https://support.claude.com/en/articles/10065433-install-claude-desktop); a repository edit alone does not establish trust in a replacement key.

## Packaging Attribution

[diazkev314/claude-desktop-aur](https://github.com/diazkev314/claude-desktop-aur/tree/76ad64b77415983bbfb2f10a68031eda7adde0ab) is the upstream packaging reference. Its attribution is independent of this repository's licensing; the MIT license here does not apply to material hosted by that project.
