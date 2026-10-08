# Legacy commitment-lock fixture

`commitment-lock.9a561b3` is the unchanged `tests/deploy/contracts/commitment-lock`
from nervosnetwork/fiber commit `9a561b3e7b786001488b61303a0663d9f4f22259`.
SHA-256: `e3686a0ae96a208365741fa127ab8a8a4f283372d14f8da3fbebd34d090573db`.

The isolated on-chain upgrade tests install it **before starting Fiber nodes**,
because the September 14 devnet snapshot already contains the new contract.
The test then upgrades the same Type ID to `source/contract/fiber/commitment-lock`.
This fixture is a deployed node-base build, not a claim about the fiber-scripts
PR base build checksum.
