# C9 / Stacks Endpoint Catalog

## Catalog status

**Entire catalog status: PROPOSED / NOT IMPLEMENTED**

This is a planning catalog for future funded C9/Stacks work. It does not modify
the Emulator endpoint inventory.

**Frozen inventory: 33 logical endpoints**

The frozen 33-endpoint inventory refers to the existing C1–C8 architecture.
Stacks/C9 is proposed grant work and is not included in the current Emulator
endpoint inventory. Therefore the current C9 endpoint count is **zero**, and
the repository currently contains no Stacks fixtures.

## Frozen observables

| ID | Observable | Intended purpose | Status |
|---|---|---|---|
| C9.1 | sBTC Supply & Peg State | Describe sBTC supply, reconciliation and peg context. | PROPOSED / NOT IMPLEMENTED |
| C9.2 | sBTC Bridge Flow | Describe normalized bridge deposits and withdrawals. | PROPOSED / NOT IMPLEMENTED |
| C9.3 | sBTC Bridge Operational State | Describe limits, chain state and signer condition. | PROPOSED / NOT IMPLEMENTED |

## C9 Native surfaces

| Proposed surface | Primary observable support | Intended future Emulator coverage | Status |
|---|---|---|---|
| `sbtc_token_supply` | C9.1 | Synthetic/replay supply and reconciliation-compatible responses. | PROPOSED / NOT IMPLEMENTED |
| `sbtc_bridge_deposits` | C9.2 | Synthetic/replay deposit lifecycle responses. | PROPOSED / NOT IMPLEMENTED |
| `sbtc_bridge_withdrawals` | C9.2 | Synthetic/replay withdrawal lifecycle responses. | PROPOSED / NOT IMPLEMENTED |
| `sbtc_bridge_limits` | C9.3 | Synthetic/replay bridge constraint responses. | PROPOSED / NOT IMPLEMENTED |
| `sbtc_bridge_chainstate` | C9.3 | Synthetic/replay bridge chain-state responses. | PROPOSED / NOT IMPLEMENTED |
| `sbtc_signer_state` | C9.3 | Synthetic/replay signer-state responses. | PROPOSED / NOT IMPLEMENTED |

## Transversal surfaces

| Proposed surface | Potential contextual consumer | Semantic constraint | Status |
|---|---|---|---|
| `sbtc_ft_transfers` | C2, C4, C8 | transfers != CVD automatically | PROPOSED / NOT IMPLEMENTED |
| `sbtc_holder_distribution` | C4, C5 | Holder concentration is not exchange flow or miner state. | PROPOSED / NOT IMPLEMENTED |
| `sbtc_dex_trades` | C1, C2, C8 | DEX trades require explicit venue and aggressor semantics. | PROPOSED / NOT IMPLEMENTED |
| `sbtc_amm_pool_state` | C1, C8 | AMM liquidity != order-book depth | PROPOSED / NOT IMPLEMENTED |
| `sbtc_lending_market_state` | C3, C6 | lending != Open Interest/Funding | PROPOSED / NOT IMPLEMENTED |
| `sbtc_protocol_liquidations` | C6, C7 | protocol liquidations != derivatives liquidations | PROPOSED / NOT IMPLEMENTED |
| `stacks_fee_state` | C6, C9 | Network fees are not market spread or volatility. | PROPOSED / NOT IMPLEMENTED |
| `stacks_mempool_activity` | C6, C9 | Mempool load is operational context, not traded volume. | PROPOSED / NOT IMPLEMENTED |

## Compatibility model

The future catalog is expected to preserve this substitution boundary:

```text
Real Stacks/Hiro/Emily provider shape
↕ compatible contract
VR1 Emulator synthetic/replay responses
↓
Processing acquisition
↓
Normalization
↓
C9 contracts
```

No route paths, request parameters, response schemas or provider assignments
are frozen by this catalog. Those details require source validation and
versioned contract design during the funded work.

"C9 owns the Stacks/sBTC data, while C1–C8 can consume C9 observables for cross-family contextual analysis."

## Inventory exclusion

None of the fourteen proposed surfaces is part of the current 33-endpoint
registry. They must not be counted as implemented endpoints until their routes,
contracts, fixtures, tests and provider compatibility have been completed and
validated in a future authorized milestone.
