"""Tiny QR encoder for the badge: byte mode, single block, mask 0.

make(text) picks the smallest of V2-M (<= 26 bytes, enough for "http://255.255.255.255"),
V3-L (<= 53) or V4-L (<= 78) and returns a list of 25/29/33 bytearrays (rows), each module 1 = dark.
V2-M output is unchanged from the original fixed encoder. Pure Python, MicroPython-safe, ~50 ms on the ESP32-S3.
Follows the layout rules of the QR spec (same algorithm as Nayuki's reference encoder).
"""

# (version, ec format bits, data codewords, ec codewords, max bytes, alignment centre)
_VARIANTS = (
    (2, 0, 28, 16, 26, 18),  # V2-M
    (3, 1, 55, 15, 53, 22),  # V3-L
    (4, 1, 80, 20, 78, 26),  # V4-L
)
MAX_BYTES = 78


def _mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a <<= 1
        if a & 0x100:
            a ^= 0x11D
        b >>= 1
    return r


def _rs(data, EC_CW):
    """Reed-Solomon remainder (EC_CW bytes) of the data codewords."""
    gen = [0] * (EC_CW - 1) + [1]
    root = 1
    for _ in range(EC_CW):
        for j in range(EC_CW):
            gen[j] = _mul(gen[j], root)
            if j + 1 < EC_CW:
                gen[j] ^= gen[j + 1]
        root = _mul(root, 2)
    rem = [0] * EC_CW
    for b in data:
        f = b ^ rem.pop(0)
        rem.append(0)
        for i in range(EC_CW):
            rem[i] ^= _mul(gen[i], f)
    return rem


def _codewords(raw, DATA_CW, EC_CW):
    bits = []

    def put(val, n):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

    put(4, 4)  # byte mode
    put(len(raw), 8)
    for c in raw:
        put(c, 8)
    put(0, min(4, DATA_CW * 8 - len(bits)))  # terminator
    while len(bits) % 8:
        bits.append(0)
    data = []
    for i in range(0, len(bits), 8):
        v = 0
        for b in bits[i : i + 8]:
            v = (v << 1) | b
        data.append(v)
    pad = 0xEC
    while len(data) < DATA_CW:
        data.append(pad)
        pad ^= 0xEC ^ 0x11
    return data + _rs(data, EC_CW)


def make(text):
    raw = text.encode()
    if len(raw) > MAX_BYTES:
        raise ValueError("QR text too long")
    for ver, ecbits, DATA_CW, EC_CW, cap, ac in _VARIANTS:
        if len(raw) <= cap:
            break
    cw = _codewords(raw, DATA_CW, EC_CW)
    n = 4 * ver + 17
    mod = [bytearray(n) for _ in range(n)]
    fn = [bytearray(n) for _ in range(n)]  # 1 = function module (not data)

    def setf(x, y, dark):
        if 0 <= x < n and 0 <= y < n:
            mod[y][x] = 1 if dark else 0
            fn[y][x] = 1

    for i in range(n):  # timing
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):  # finders + separators
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                setf(cx + dx, cy + dy, max(abs(dx), abs(dy)) not in (2, 4))
    for dy in range(-2, 3):  # alignment pattern at (ac, ac)
        for dx in range(-2, 3):
            setf(ac + dx, ac + dy, max(abs(dx), abs(dy)) != 1)

    # Format info: ECC level bits + mask 0, BCH(15,5), xor 0x5412.
    data = ecbits << 3
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    bits = ((data << 10) | rem) ^ 0x5412

    def bit(i):
        return (bits >> i) & 1

    for i in range(6):
        setf(8, i, bit(i))
    setf(8, 7, bit(6))
    setf(8, 8, bit(7))
    setf(7, 8, bit(8))
    for i in range(9, 15):
        setf(14 - i, 8, bit(i))
    for i in range(8):
        setf(n - 1 - i, 8, bit(i))
    for i in range(8, 15):
        setf(8, n - 15 + i, bit(i))
    setf(8, n - 8, True)  # always-dark module

    # Place data bits in the zigzag order, applying mask 0 ((x + y) even => invert).
    i = 0
    total = len(cw) * 8
    right = n - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(n):
            for j in range(2):
                x = right - j
                y = (n - 1 - vert) if ((right + 1) & 2) == 0 else vert
                if not fn[y][x] and i < total:
                    b = (cw[i >> 3] >> (7 - (i & 7))) & 1
                    mod[y][x] = b ^ (1 if (x + y) % 2 == 0 else 0)
                    i += 1
                elif not fn[y][x] and (x + y) % 2 == 0:
                    mod[y][x] = 1  # remainder bits are 0, masked
        right -= 2
    return mod
