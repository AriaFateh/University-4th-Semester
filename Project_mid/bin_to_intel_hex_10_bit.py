def binary_to_intel_hex(binary_lines):
    intel_hex = []
    address = 0

    for line in binary_lines:

        value = int(line.strip(), 2)

        if value > 0x3FF:
            raise ValueError("Value exceeds 10 bits")

        low = value & 0xFF
        high = (value >> 8) & 0x03

        byte_count = 2
        record_type = 0

        record = [
            byte_count,
            (address >> 8) & 0xFF,
            address & 0xFF,
            record_type,
            low,
            high
        ]

        checksum = ((~sum(record) + 1) & 0xFF)

        hex_line = (
            ":{:02X}{:04X}{:02X}".format(
                byte_count,
                address,
                record_type
            )
            + "{:02X}{:02X}".format(low, high)
            + "{:02X}".format(checksum)
        )

        intel_hex.append(hex_line)

        address += 2

    intel_hex.append(":00000001FF")

    return "\n".join(intel_hex)

#=================== Test ========================


binary_lines = """0000001111
1111000000
0000001111
1111000000
0000011111
1111100111
0000011000
0000011100
0000000000
0000000100
0000001000
0000001100
0000010000
0000010100
0000011000
0000011100
0000000000
0000000100
0000001000
0000001100
0000010000
0000010100
0000011000
0000011100
0000000000
0000000100
0000001000
0000001100
0000010000
0000010100
0000011100""".splitlines()

result = binary_to_intel_hex(binary_lines)
print(result)