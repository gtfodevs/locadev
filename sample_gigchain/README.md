# GigChain localnet (locadev profile `gigchain`)

Runs GigChain’s Cosmos SDK single-validator node (`gigchaind`) in Docker.

> Not Azure Cosmos DB. That is locadev profile `cosmos` on port 8081.

## Start

```bash
# from locadev — chain source defaults to sibling ../gigchain/chain
GIGCHAIN_CHAIN_DIR=../gigchain/chain ./scripts/start.sh gigchain
```

Combine with auth fakes if needed:

```bash
GIGCHAIN_CHAIN_DIR=../gigchain/chain ./scripts/start.sh gigchain cloudflare oauth
```

## Ports

| Port | Service |
|------|---------|
| **26657** | CometBFT RPC |
| **1317** | Cosmos REST |
| **9090** | gRPC |
| 26656 | P2P |

## Smoke

```bash
curl -s http://127.0.0.1:26657/status | head
curl -s http://127.0.0.1:1317/cosmos/base/tendermint/v1beta1/node_info | head
```

## Notes

- Image: `gigchain/chain/Dockerfile.locadev` builds `gigchaind`, then `scripts/localnet-entrypoint.sh` inits genesis (alice/bob) and starts the node.
- Default chain id: `localgigchain`. Set `GIGCHAIN_RESET=0` to keep `/data` across restarts.
- Host alternative: `cd ../gigchain/chain && make localnet` (Ignite on the host).
