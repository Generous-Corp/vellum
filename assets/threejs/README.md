# Three.js runtime payload

The files beside this one are the Three.js 0.183.1 runtime a scripted view
loads as ES modules: the three.webgpu build with its core and module
companions, the OrbitControls, GLTFLoader, BufferGeometryUtils and
SkeletonUtils add-ons, the MIT `LICENSE` and `package.json`. They are
unmodified bytes of `mrdoob/three.js` at revision
`077dd13c0e869d9f3dbe55875686f920367de457`.

`payload-manifest.json` pins each file by SHA-256. `scripts/check_threejs_payload.py`
refuses a missing, extra or changed file, and refuses a manifest that differs
from the digest recorded in `provenance/third-party-lock.json`. The root
`CMakeLists.txt` runs the same check at configure time, so a build never
proceeds on a payload that does not match its pin.

To move to another release, replace the files with the new revision's bytes,
update the manifest and the lock entry together, and update `DEPENDENCIES.md`
and `NOTICE.md`.
