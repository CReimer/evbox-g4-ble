# Dependency and brand evidence

Reviewed 2026-10-08 for the Bronze dependency-transparency and brands rules.

## Runtime dependency

The integration manifest declares `aioftp>=0.21.3`. This preserves compatibility with the version shared by Home Assistant; it does not install a private conflicting version. BLE, aiohttp and Home Assistant helper dependencies come from the declared `bluetooth` core integration or Home Assistant itself.

| Requirement | Evidence |
|---|---|
| Public source and OSI license | [aioftp source](https://github.com/aio-libs/aioftp), [Apache-2.0 license](https://github.com/aio-libs/aioftp/blob/master/LICENSE), [PyPI license metadata](https://pypi.org/project/aioftp/0.27.2/) |
| Available on PyPI | [Minimum 0.21.3](https://pypi.org/project/aioftp/0.21.3/) and [0.27.2](https://pypi.org/project/aioftp/0.27.2/) |
| Source matches published versions | [0.21.3 source tag](https://github.com/aio-libs/aioftp/tree/0.21.3), [0.27.2 source tag](https://github.com/aio-libs/aioftp/tree/0.27.2) |
| Public build and publication | [0.21.3 workflow](https://github.com/aio-libs/aioftp/blob/0.21.3/.github/workflows/ci.yml), [0.27.2 publishing workflow at the attested commit](https://github.com/aio-libs/aioftp/blob/226ad64264c5dd7e93a56fe92544b7cf0cc5da50/.github/workflows/ci-cd.yml) |
| Release provenance | [PyPI 0.27.2 file attestations](https://pypi.org/project/aioftp/0.27.2/#files) identify GitHub Actions, the publishing commit and workflow. Older 0.21.3 predates Trusted Publishing; its public workflow builds and publishes from master with matching release tags. |

No vendor firmware or application code is a Python dependency of this integration. Firmware metadata and selected firmware images are fetched on demand; vendor binaries are not distributed in the repository.

## Local brand images

Since HA 2026.3, custom integrations may ship a `brand/` directory. See the [official announcement](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api/) and [brand image specifications](https://github.com/home-assistant/brands#image-specification). This integration requires HA 2026.8 or later, so the local mechanism is available on every supported version.

The integration includes `brand/icon.png` (256 × 256) and `brand/icon@2x.png` (512 × 512). Home Assistant uses the icon as the logo fallback. Metadata tests decode and verify these PNGs and dimensions. Trademark attribution is included in `brand/README.md` and the project README; no official HA artwork or quality badge is used.

An additional entry in the legacy custom-integrations folder of the brands repository is unnecessary for this supported HA range. This mechanism supplies branding; it does not grant an official quality tier.
