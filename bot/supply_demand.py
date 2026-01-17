from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional, TypeVar

from bot.data_providers import FmpClient
from bot.logger import BotLog
from bot.models import OwnershipSnapshot, SharesOutstandingSnapshot


@dataclass(frozen=True)
class SupplyDemandResult:
    symbol: str
    shares_outstanding: int
    institutional_owners: int
    institutional_prev: int
    institutional_latest: int


T = TypeVar("T")


def _latest_snapshot(snapshots: List[T], as_of: date) -> Optional[T]:
    eligible = [snap for snap in snapshots if snap.accepted_date <= as_of]
    if not eligible:
        return None
    return max(eligible, key=lambda snap: snap.accepted_date)


def _latest_two_snapshots(snapshots: List[T], as_of: date) -> List[T]:
    eligible = [snap for snap in snapshots if snap.accepted_date <= as_of]
    eligible.sort(key=lambda snap: snap.accepted_date)
    return eligible[-2:] if len(eligible) >= 2 else []


def evaluate_supply_demand(
    symbol: str,
    as_of: date,
    fmp: FmpClient,
    log: BotLog,
) -> Optional[SupplyDemandResult]:
    shares_history = fmp.shares_outstanding(symbol)
    ownership_history = fmp.institutional_ownership(symbol)
    latest_shares = _latest_snapshot(shares_history, as_of)
    if latest_shares is None:
        log.info(f"{symbol} rejected: missing shares outstanding data.")
        return None
    latest_two_owners = _latest_two_snapshots(ownership_history, as_of)
    if len(latest_two_owners) < 2:
        log.info(f"{symbol} rejected: missing institutional ownership data.")
        return None
    prev_owners, latest_owners = latest_two_owners
    if (
        latest_shares.shares_outstanding <= 0
        or latest_owners.institutional_owners <= 0
        or prev_owners.institutional_owners <= 0
    ):
        log.info(f"{symbol} rejected: invalid supply/demand figures.")
        return None
    if latest_owners.institutional_owners < prev_owners.institutional_owners:
        log.info(
            f"{symbol} rejected: institutional sponsorship declined (prev={prev_owners.institutional_owners}, latest={latest_owners.institutional_owners})."
        )
        return None
    return SupplyDemandResult(
        symbol=symbol,
        shares_outstanding=latest_shares.shares_outstanding,
        institutional_owners=latest_owners.institutional_owners,
        institutional_prev=prev_owners.institutional_owners,
        institutional_latest=latest_owners.institutional_owners,
    )
