"""Constants for the Probe Plus device parsers."""

# Shared across FM standard and plus (probe voltage byte, millivolts → volts).
STD_PROBE_VOLTAGE_FACTOR = 0.03125
RELAY_VOLTAGE_DIVISOR = 1000.0

# FM standard (no "+" in the advertised name): OEM "old probe agreement".
STD_TEMP_FACTOR = 0.0625
STD_TEMP_OFFSET = 50.0625

# FM plus (name contains "+"): tenths of a degree, little-endian.
PLUS_TEMP_DIVISOR = 10.0
FM2_TARGET_UNSET = 0xFFFF
# Offsets of the two channel targets within the 41-byte STATUS frame (0x00 0x05).
PLUS_STATUS_TARGET_OFFSETS = (11, 20)
# Offsets of the two channel targets within the TARGET notification (0x00 0x03).
PLUS_TARGET_FRAME_OFFSETS = (3, 5)

# Relay frames delimit probe broadcast cycles; mark a slot offline after this
# many consecutive cycles without a probe frame for that slot.
PROBE_OFFLINE_MISS_THRESHOLD = 5
