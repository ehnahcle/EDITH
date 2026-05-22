"""Strategy registry."""

from .volatility_breakout import volatility_breakout
from .gap_reversal import gap_reversal
from .momentum import momentum_5d
from .rsi_meanrev import rsi_oversold
from .volume_breakout import volume_breakout
from .new_high_52w import new_high_52w
from .disparity import disparity_meanrev

STRATEGIES = {
    "VolBreakout":   volatility_breakout,
    "GapReversal":   gap_reversal,
    "Momentum5":     momentum_5d,
    "RSIOversold":   rsi_oversold,
    "VolumeBreak":   volume_breakout,
    "NewHigh52w":    new_high_52w,
    "Disparity":     disparity_meanrev,
}
