# C9 / Stacks Endpoint Catalog

## Catalog status

**Status: PROPOSED / NOT IMPLEMENTED**

This is the canonical pre-implementation catalog for future funded C9/Stacks
work. It does not modify the Emulator endpoint inventory.

**Frozen inventory: 33 logical endpoints**

The existing registry contains only C1–C8 endpoints: 20 CoinGlass,
4 CryptoQuant and 9 Glassnode. The current C9/Stacks endpoint count is **zero**.

The C9 pre-implementation catalog freezes 14 proposed logical source surfaces — 6 Core and 8 transversal — but none is part of the current 33-endpoint Emulator registry.

The 14 IDs are proposed logical source surfaces. They are not registered
endpoints. No Stacks HTTP routes, Stacks fixtures or Stacks provider currently
exist, and Milestone 1 has not started.

## Ownership rule

"C9 owns the Stacks/sBTC data, while C1–C8 can consume C9 observables for cross-family contextual analysis."

C9 therefore never appears as a value in the **Contextual consumers C1–C8**
column. C9 owns the source data and C9 observable construction; the consumer
column records only optional downstream contextual use by C1–C8.

## Frozen observables

| ID | Observable | Intended purpose | Status |
|---|---|---|---|
| C9.1 | sBTC Supply & Peg State | Describe sBTC supply, reconciliation and peg context. | PROPOSED / NOT IMPLEMENTED |
| C9.2 | sBTC Bridge Flow | Describe normalized bridge deposits and withdrawals. | PROPOSED / NOT IMPLEMENTED |
| C9.3 | sBTC Bridge Operational State | Describe limits, chain state and signer condition. | PROPOSED / NOT IMPLEMENTED |

## Core proposed logical source surfaces

| Proposed logical source surface | C9 observable ownership/support | Contextual consumers C1–C8 | Intended future Emulator purpose | Status |
|---|---|---|---|---|
| `sbtc_token_supply` | C9.1 | C5 | Supply and reconciliation-compatible synthetic/replay responses. | PROPOSED / NOT IMPLEMENTED |
| `sbtc_bridge_deposits` | C9.2; may contribute to C9.3 where operational fields apply | C4, C5, C6 | Deposit lifecycle and operational-field scenarios. | PROPOSED / NOT IMPLEMENTED |
| `sbtc_bridge_withdrawals` | C9.2; may contribute to C9.3 where operational fields apply | C4, C5, C6 | Withdrawal lifecycle and operational-field scenarios. | PROPOSED / NOT IMPLEMENTED |
| `sbtc_bridge_limits` | C9.3 | C4, C6 | Bridge-limit and capacity-constraint scenarios. | PROPOSED / NOT IMPLEMENTED |
| `sbtc_bridge_chainstate` | C9.3 | C4, C5, C6 | Bridge chain-state and availability scenarios. | PROPOSED / NOT IMPLEMENTED |
| `sbtc_signer_state` | C9.3 | C5, C6 | Publicly observable signer/registry-state synthetic/replay scenarios. | PROPOSED / NOT IMPLEMENTED |

For `sbtc_signer_state`: **Do not infer signer availability, consensus, security, or custody health unless supported by a validated protocol-defined measure.**

## Transversal proposed logical source surfaces

| Proposed logical source surface | Contextual consumers C1–C8 | Semantic constraint or intended context | Status |
|---|---|---|---|
| `sbtc_ft_transfers` | C2, C5 | transfers != CVD automatically | PROPOSED / NOT IMPLEMENTED |
| `sbtc_holder_distribution` | C5 | Holder concentration is an on-chain distribution context. | PROPOSED / NOT IMPLEMENTED |
| `sbtc_dex_trades` | C1, C2, C8 | DEX trades require explicit venue and aggressor semantics. | PROPOSED / NOT IMPLEMENTED |
| `sbtc_amm_pool_state` | C1, C8 | AMM liquidity != order-book depth | PROPOSED / NOT IMPLEMENTED |
| `sbtc_lending_market_state` | C3, C6 | lending != Open Interest/Funding | PROPOSED / NOT IMPLEMENTED |
| `sbtc_protocol_liquidations` | C6, C7 | protocol liquidations != derivatives liquidations | PROPOSED / NOT IMPLEMENTED |
| `stacks_fee_state` | C6 | Network fees provide congestion/cost context, not market spread. | PROPOSED / NOT IMPLEMENTED |
| `stacks_mempool_activity` | C6 | Mempool load provides network-stress context, not traded volume. | PROPOSED / NOT IMPLEMENTED |

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
versioned contract design during future funded work.

## Inventory exclusion

None of the 14 proposed logical source surfaces is part of the current
33-endpoint registry. They must not be counted as implemented endpoints until
their routes, contracts, fixtures, tests and provider compatibility have been
completed and validated in a future authorized milestone.
