# C9 / Stacks Technical Specification

## Status and scope

**Status: PROPOSED / NOT IMPLEMENTED**

This document defines a future C9/Stacks extension for the TradELATIN VR1
Emulator. It is grant-scope documentation only. It does not add endpoints,
providers, fixtures, public probes, or Stacks API calls, and it does not
implement Milestone 1.

**Frozen inventory: 33 logical endpoints**

The frozen 33-endpoint inventory refers to the existing C1–C8 architecture.
Stacks/C9 is proposed grant work and is not included in the current Emulator
endpoint inventory. The current Emulator has no C9 endpoints and no Stacks
fixtures.

## Architectural boundary

The proposed end-to-end architecture is:

```text
Stacks/sBTC
→ Adapter
→ Normalization
→ Versioned C9 Contracts
→ Financial Observables
→ HMI
```

C9 is the ownership boundary for Stacks/sBTC acquisition, normalization and
versioned observability. The HMI consumes financial observables rather than
raw provider payloads.

"C9 owns the Stacks/sBTC data, while C1–C8 can consume C9 observables for cross-family contextual analysis."

Cross-family consumption does not transfer semantic ownership to C1–C8 and
must not silently reinterpret on-chain activity as exchange or derivatives
market data.

## Future Emulator role

When funded and implemented, the Emulator would reproduce provider-compatible
response shapes and deterministic replay/synthetic behavior behind the same
contract boundary used by real acquisition:

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

This future role would permit Processing and Integration tests without
requiring live provider availability. The Emulator would model transport and
domain scenarios while remaining substitutable with real provider adapters.
No such Stacks behavior exists in the current repository.

## Frozen C9 financial observables

The proposed C9 scope freezes three financial observables:

1. **C9.1 sBTC Supply & Peg State** — observable supply, mint/burn reconciliation,
   and peg-state context derived from normalized Stacks/sBTC evidence.
2. **C9.2 sBTC Bridge Flow** — normalized deposit and withdrawal activity across
   the sBTC bridge, preserving direction, amount, status and time.
3. **C9.3 sBTC Bridge Operational State** — bridge limits, chain state and signer
   condition needed to describe operational availability and constraints.

These observable names are frozen for documentation and contract design. Their
runtime contracts, endpoint paths, provider adapters, fixtures and replay
engines remain **PROPOSED / NOT IMPLEMENTED**.

## Proposed data surfaces

Every surface below is **PROPOSED / NOT IMPLEMENTED**.

### C9 Native

- `sbtc_token_supply`
- `sbtc_bridge_deposits`
- `sbtc_bridge_withdrawals`
- `sbtc_bridge_limits`
- `sbtc_bridge_chainstate`
- `sbtc_signer_state`

The native surfaces are intended to support the three frozen C9 observables.
They do not exist in `app/endpoint_registry.py` and have no Emulator routes
or fixtures.

### Transversal

- `sbtc_ft_transfers`
- `sbtc_holder_distribution`
- `sbtc_dex_trades`
- `sbtc_amm_pool_state`
- `sbtc_lending_market_state`
- `sbtc_protocol_liquidations`
- `stacks_fee_state`
- `stacks_mempool_activity`

Transversal surfaces would supply contextual observables that other VR1
families may consume through versioned contracts. They remain owned and
normalized by C9.

## Cross-family use and semantic safeguards

Proposed C9 observables could provide context to the existing C1–C8 families,
but the following distinctions are mandatory:

- Token transfers may inform C2 flow context, but **transfers != CVD automatically**.
  CVD requires an aggressor-side trade model; raw transfers do not encode it.
- Lending activity may contextualize C3 leverage, but **lending != Open
  Interest/Funding**. On-chain debt positions are not derivatives OI or funding
  rates.
- AMM reserves and pool state may contextualize C8 liquidity, but **AMM
  liquidity != order-book depth**. Constant-function pools do not expose a
  central-limit-order-book depth ladder.
- Protocol liquidation events may contextualize C7 stress, but **protocol
  liquidations != derivatives liquidations**. Their triggers, collateral rules
  and execution mechanics differ.

Any future derived mapping must identify its source surface, transformation,
contract version and limitations. It must never relabel a C9 primitive as a
native C1–C8 measurement without an explicit validated derivation.

## Future contract requirements

Future funded implementation should define, before adding routes:

- provider-shape adapters for the approved Stacks/Hiro/Emily sources;
- normalized timestamps, identifiers, assets, amounts, units and status enums;
- versioned C9 schemas with provenance and data-quality metadata;
- deterministic seeds and scenario IDs for synthetic responses;
- replay ordering, cursor and pagination behavior;
- error, stale-data, reorganization and provider-unavailable scenarios;
- contract parity tests between real-provider shapes and Emulator responses;
- explicit cross-family derivations that enforce the semantic safeguards above.

These requirements are design constraints, not evidence of current
implementation.
