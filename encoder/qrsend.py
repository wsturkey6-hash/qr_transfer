#!/usr/bin/env python3
"""qrsend：把一個檔案轉成一串 QR code，在瀏覽器裡輪播，給手機端 decoder 掃描還原。

用法：
    python3 qrsend.py FILE [--chunk 700] [--fps 5] [--ecc L]
    python3 qrsend.py FILE --export-frames DIR    # 匯出 PGM 影格（測試用）

只用 Python 標準函式庫，支援 Python 3.6 以上。格式見 PROTOCOL.md。
"""


# ==== 內嵌：Nayuki QR Code generator library（MIT）====
# 來源：https://github.com/nayuki/QR-Code-generator/blob/master/python/qrcodegen.py
# 修改：移除型別註記與 typing 相關 import，以相容 Python 3.6。程式邏輯未改動。

# 
# QR Code generator library (Python)
# 
# Copyright (c) Project Nayuki. (MIT License)
# https://www.nayuki.io/page/qr-code-generator-library
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
# the Software, and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
# - The above copyright notice and this permission notice shall be included in
#   all copies or substantial portions of the Software.
# - The Software is provided "as is", without warranty of any kind, express or
#   implied, including but not limited to the warranties of merchantability,
#   fitness for a particular purpose and noninfringement. In no event shall the
#   authors or copyright holders be liable for any claim, damages or other
#   liability, whether in an action of contract, tort or otherwise, arising from,
#   out of or in connection with the Software or the use or other dealings in the
#   Software.
# 

import collections, itertools, re


# ---- QR Code symbol class ----

class QrCode:
	"""A QR Code symbol, which is a type of two-dimension barcode.
	Invented by Denso Wave and described in the ISO/IEC 18004 standard.
	Instances of this class represent an immutable square grid of dark and light cells.
	The class provides static factory functions to create a QR Code from text or binary data.
	The class covers the QR Code Model 2 specification, supporting all versions (sizes)
	from 1 to 40, all 4 error correction levels, and 4 character encoding modes.
	
	Ways to create a QR Code object:
	- High level: Take the payload data and call QrCode.encode_text() or QrCode.encode_binary().
	- Mid level: Custom-make the list of segments and call QrCode.encode_segments().
	- Low level: Custom-make the array of data codeword bytes (including
	  segment headers and final padding, excluding error correction codewords),
	  supply the appropriate version number, and call the QrCode() constructor.
	(Note that all ways require supplying the desired error correction level.)"""
	
	# ---- Static factory functions (high level) ----
	
	@staticmethod
	def encode_text(text, ecl):
		"""Returns a QR Code representing the given Unicode text string at the given error correction level.
		As a conservative upper bound, this function is guaranteed to succeed for strings that have 738 or fewer
		Unicode code points (not UTF-16 code units) if the low error correction level is used. The smallest possible
		QR Code version is automatically chosen for the output. The ECC level of the result may be higher than the
		ecl argument if it can be done without increasing the version."""
		segs = QrSegment.make_segments(text)
		return QrCode.encode_segments(segs, ecl)
	
	
	@staticmethod
	def encode_binary(data, ecl):
		"""Returns a QR Code representing the given binary data at the given error correction level.
		This function always encodes using the binary segment mode, not any text mode. The maximum number of
		bytes allowed is 2953. The smallest possible QR Code version is automatically chosen for the output.
		The ECC level of the result may be higher than the ecl argument if it can be done without increasing the version."""
		return QrCode.encode_segments([QrSegment.make_bytes(data)], ecl)
	
	
	# ---- Static factory functions (mid level) ----
	
	@staticmethod
	def encode_segments(segs, ecl, minversion = 1, maxversion = 40, mask = -1, boostecl = True):
		"""Returns a QR Code representing the given segments with the given encoding parameters.
		The smallest possible QR Code version within the given range is automatically
		chosen for the output. Iff boostecl is true, then the ECC level of the result
		may be higher than the ecl argument if it can be done without increasing the
		version. The mask number is either between 0 to 7 (inclusive) to force that
		mask, or -1 to automatically choose an appropriate mask (which may be slow).
		This function allows the user to create a custom sequence of segments that switches
		between modes (such as alphanumeric and byte) to encode text in less space.
		This is a mid-level API; the high-level API is encode_text() and encode_binary()."""
		
		if not (QrCode.MIN_VERSION <= minversion <= maxversion <= QrCode.MAX_VERSION) or not (-1 <= mask <= 7):
			raise ValueError("Invalid value")
		
		# Find the minimal version number to use
		for version in range(minversion, maxversion + 1):
			datacapacitybits = QrCode._get_num_data_codewords(version, ecl) * 8  # Number of data bits available
			datausedbits = QrSegment.get_total_bits(segs, version)
			if (datausedbits is not None) and (datausedbits <= datacapacitybits):
				break  # This version number is found to be suitable
			if version >= maxversion:  # All versions in the range could not fit the given data
				msg = "Segment too long"
				if datausedbits is not None:
					msg = f"Data length = {datausedbits} bits, Max capacity = {datacapacitybits} bits"
				raise DataTooLongError(msg)
		assert datausedbits is not None
		
		# Increase the error correction level while the data still fits in the current version number
		for newecl in (QrCode.Ecc.MEDIUM, QrCode.Ecc.QUARTILE, QrCode.Ecc.HIGH):  # From low to high
			if boostecl and (datausedbits <= QrCode._get_num_data_codewords(version, newecl) * 8):
				ecl = newecl
		
		# Concatenate all segments to create the data bit string
		bb = _BitBuffer()
		for seg in segs:
			bb.append_bits(seg.get_mode().get_mode_bits(), 4)
			bb.append_bits(seg.get_num_chars(), seg.get_mode().num_char_count_bits(version))
			bb.extend(seg._bitdata)
		assert len(bb) == datausedbits
		
		# Add terminator and pad up to a byte if applicable
		datacapacitybits = QrCode._get_num_data_codewords(version, ecl) * 8
		assert len(bb) <= datacapacitybits
		bb.append_bits(0, min(4, datacapacitybits - len(bb)))
		bb.append_bits(0, -len(bb) % 8)  # Note: Python's modulo on negative numbers behaves better than C family languages
		assert len(bb) % 8 == 0
		
		# Pad with alternating bytes until data capacity is reached
		for padbyte in itertools.cycle((0xEC, 0x11)):
			if len(bb) >= datacapacitybits:
				break
			bb.append_bits(padbyte, 8)
		
		# Pack bits into bytes in big endian
		datacodewords = bytearray([0] * (len(bb) // 8))
		for (i, bit) in enumerate(bb):
			datacodewords[i >> 3] |= bit << (7 - (i & 7))
		
		# Create the QR Code object
		return QrCode(version, ecl, datacodewords, mask)
	
	
	# ---- Private fields ----
	
	# The version number of this QR Code, which is between 1 and 40 (inclusive).
	# This determines the size of this barcode.
	
	# The width and height of this QR Code, measured in modules, between
	# 21 and 177 (inclusive). This is equal to version * 4 + 17.
	
	# The error correction level used in this QR Code.
	
	# The index of the mask pattern used in this QR Code, which is between 0 and 7 (inclusive).
	# Even if a QR Code is created with automatic masking requested (mask = -1),
	# the resulting object still has a mask value between 0 and 7.
	
	# The modules of this QR Code (False = light, True = dark).
	# Immutable after constructor finishes. Accessed through get_module().
	
	# Indicates function modules that are not subjected to masking. Discarded when constructor finishes.
	
	
	# ---- Constructor (low level) ----
	
	def __init__(self, version, errcorlvl, datacodewords, msk):
		"""Creates a new QR Code with the given version number,
		error correction level, data codeword bytes, and mask number.
		This is a low-level API that most users should not use directly.
		A mid-level API is the encode_segments() function."""
		
		# Check scalar arguments and set fields
		if not (QrCode.MIN_VERSION <= version <= QrCode.MAX_VERSION):
			raise ValueError("Version value out of range")
		if not (-1 <= msk <= 7):
			raise ValueError("Mask value out of range")
		
		self._version = version
		self._size = version * 4 + 17
		self._errcorlvl = errcorlvl
		
		# Initialize both grids to be size*size arrays of Boolean false
		self._modules    = [[False] * self._size for _ in range(self._size)]  # Initially all light
		self._isfunction = [[False] * self._size for _ in range(self._size)]
		
		# Compute ECC, draw modules
		self._draw_function_patterns()
		allcodewords = self._add_ecc_and_interleave(bytearray(datacodewords))
		self._draw_codewords(allcodewords)
		
		# Do masking
		if msk == -1:  # Automatically choose best mask
			minpenalty = 1 << 32
			for i in range(8):
				self._apply_mask(i)
				self._draw_format_bits(i)
				penalty = self._get_penalty_score()
				if penalty < minpenalty:
					msk = i
					minpenalty = penalty
				self._apply_mask(i)  # Undoes the mask due to XOR
		assert 0 <= msk <= 7
		self._mask = msk
		self._apply_mask(msk)  # Apply the final choice of mask
		self._draw_format_bits(msk)  # Overwrite old format bits
		
		del self._isfunction
	
	
	# ---- Accessor methods ----
	
	def get_version(self):
		"""Returns this QR Code's version number, in the range [1, 40]."""
		return self._version
	
	def get_size(self):
		"""Returns this QR Code's size, in the range [21, 177]."""
		return self._size
	
	def get_error_correction_level(self):
		"""Returns this QR Code's error correction level."""
		return self._errcorlvl
	
	def get_mask(self):
		"""Returns this QR Code's mask, in the range [0, 7]."""
		return self._mask
	
	def get_module(self, x, y):
		"""Returns the color of the module (pixel) at the given coordinates, which is False
		for light or True for dark. The top left corner has the coordinates (x=0, y=0).
		If the given coordinates are out of bounds, then False (light) is returned."""
		return (0 <= x < self._size) and (0 <= y < self._size) and self._modules[y][x]
	
	
	# ---- Private helper methods for constructor: Drawing function modules ----
	
	def _draw_function_patterns(self):
		"""Reads this object's version field, and draws and marks all function modules."""
		# Draw horizontal and vertical timing patterns
		for i in range(self._size):
			self._set_function_module(6, i, i % 2 == 0)
			self._set_function_module(i, 6, i % 2 == 0)
		
		# Draw 3 finder patterns (all corners except bottom right; overwrites some timing modules)
		self._draw_finder_pattern(3, 3)
		self._draw_finder_pattern(self._size - 4, 3)
		self._draw_finder_pattern(3, self._size - 4)
		
		# Draw numerous alignment patterns
		alignpatpos = self._get_alignment_pattern_positions()
		numalign = len(alignpatpos)
		skips = ((0, 0), (0, numalign - 1), (numalign - 1, 0))
		for i in range(numalign):
			for j in range(numalign):
				if (i, j) not in skips:  # Don't draw on the three finder corners
					self._draw_alignment_pattern(alignpatpos[i], alignpatpos[j])
		
		# Draw configuration data
		self._draw_format_bits(0)  # Dummy mask value; overwritten later in the constructor
		self._draw_version()
	
	
	def _draw_format_bits(self, mask):
		"""Draws two copies of the format bits (with its own error correction code)
		based on the given mask and this object's error correction level field."""
		# Calculate error correction code and pack bits
		data = self._errcorlvl.formatbits << 3 | mask  # errCorrLvl is uint2, mask is uint3
		rem = data
		for _ in range(10):
			rem = (rem << 1) ^ ((rem >> 9) * 0x537)
		bits = (data << 10 | rem) ^ 0x5412  # uint15
		assert bits >> 15 == 0
		
		# Draw first copy
		for i in range(0, 6):
			self._set_function_module(8, i, _get_bit(bits, i))
		self._set_function_module(8, 7, _get_bit(bits, 6))
		self._set_function_module(8, 8, _get_bit(bits, 7))
		self._set_function_module(7, 8, _get_bit(bits, 8))
		for i in range(9, 15):
			self._set_function_module(14 - i, 8, _get_bit(bits, i))
		
		# Draw second copy
		for i in range(0, 8):
			self._set_function_module(self._size - 1 - i, 8, _get_bit(bits, i))
		for i in range(8, 15):
			self._set_function_module(8, self._size - 15 + i, _get_bit(bits, i))
		self._set_function_module(8, self._size - 8, True)  # Always dark
	
	
	def _draw_version(self):
		"""Draws two copies of the version bits (with its own error correction code),
		based on this object's version field, iff 7 <= version <= 40."""
		if self._version < 7:
			return
		
		# Calculate error correction code and pack bits
		rem = self._version  # version is uint6, in the range [7, 40]
		for _ in range(12):
			rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
		bits = self._version << 12 | rem  # uint18
		assert bits >> 18 == 0
		
		# Draw two copies
		for i in range(18):
			bit = _get_bit(bits, i)
			a = self._size - 11 + i % 3
			b = i // 3
			self._set_function_module(a, b, bit)
			self._set_function_module(b, a, bit)
	
	
	def _draw_finder_pattern(self, x, y):
		"""Draws a 9*9 finder pattern including the border separator,
		with the center module at (x, y). Modules can be out of bounds."""
		for dy in range(-4, 5):
			for dx in range(-4, 5):
				xx, yy = x + dx, y + dy
				if (0 <= xx < self._size) and (0 <= yy < self._size):
					# Chebyshev/infinity norm
					self._set_function_module(xx, yy, max(abs(dx), abs(dy)) not in (2, 4))
	
	
	def _draw_alignment_pattern(self, x, y):
		"""Draws a 5*5 alignment pattern, with the center module
		at (x, y). All modules must be in bounds."""
		for dy in range(-2, 3):
			for dx in range(-2, 3):
				self._set_function_module(x + dx, y + dy, max(abs(dx), abs(dy)) != 1)
	
	
	def _set_function_module(self, x, y, isdark):
		"""Sets the color of a module and marks it as a function module.
		Only used by the constructor. Coordinates must be in bounds."""
		assert type(isdark) is bool
		self._modules[y][x] = isdark
		self._isfunction[y][x] = True
	
	
	# ---- Private helper methods for constructor: Codewords and masking ----
	
	def _add_ecc_and_interleave(self, data):
		"""Returns a new byte string representing the given data with the appropriate error correction
		codewords appended to it, based on this object's version and error correction level."""
		version = self._version
		assert len(data) == QrCode._get_num_data_codewords(version, self._errcorlvl)
		
		# Calculate parameter numbers
		numblocks = QrCode._NUM_ERROR_CORRECTION_BLOCKS[self._errcorlvl.ordinal][version]
		blockecclen = QrCode._ECC_CODEWORDS_PER_BLOCK  [self._errcorlvl.ordinal][version]
		rawcodewords = QrCode._get_num_raw_data_modules(version) // 8
		numshortblocks = numblocks - rawcodewords % numblocks
		shortblocklen = rawcodewords // numblocks
		
		# Split data into blocks and append ECC to each block
		blocks = []
		rsdiv = QrCode._reed_solomon_compute_divisor(blockecclen)
		k = 0
		for i in range(numblocks):
			dat = data[k : k + shortblocklen - blockecclen + (0 if i < numshortblocks else 1)]
			k += len(dat)
			ecc = QrCode._reed_solomon_compute_remainder(dat, rsdiv)
			if i < numshortblocks:
				dat.append(0)
			blocks.append(dat + ecc)
		assert k == len(data)
		
		# Interleave (not concatenate) the bytes from every block into a single sequence
		result = bytearray()
		for i in range(len(blocks[0])):
			for (j, blk) in enumerate(blocks):
				# Skip the padding byte in short blocks
				if (i != shortblocklen - blockecclen) or (j >= numshortblocks):
					result.append(blk[i])
		assert len(result) == rawcodewords
		return result
	
	
	def _draw_codewords(self, data):
		"""Draws the given sequence of 8-bit codewords (data and error correction) onto the entire
		data area of this QR Code. Function modules need to be marked off before this is called."""
		assert len(data) == QrCode._get_num_raw_data_modules(self._version) // 8
		
		i = 0  # Bit index into the data
		# Do the funny zigzag scan
		for right in range(self._size - 1, 0, -2):  # Index of right column in each column pair
			if right <= 6:
				right -= 1
			for vert in range(self._size):  # Vertical counter
				for j in range(2):
					x = right - j  # Actual x coordinate
					upward = (right + 1) & 2 == 0
					y = (self._size - 1 - vert) if upward else vert  # Actual y coordinate
					if (not self._isfunction[y][x]) and (i < len(data) * 8):
						self._modules[y][x] = _get_bit(data[i >> 3], 7 - (i & 7))
						i += 1
					# If this QR Code has any remainder bits (0 to 7), they were assigned as
					# 0/false/light by the constructor and are left unchanged by this method
		assert i == len(data) * 8
	
	
	def _apply_mask(self, mask):
		"""XORs the codeword modules in this QR Code with the given mask pattern.
		The function modules must be marked and the codeword bits must be drawn
		before masking. Due to the arithmetic of XOR, calling _apply_mask() with
		the same mask value a second time will undo the mask. A final well-formed
		QR Code needs exactly one (not zero, two, etc.) mask applied."""
		if not (0 <= mask <= 7):
			raise ValueError("Mask value out of range")
		masker = QrCode._MASK_PATTERNS[mask]
		for y in range(self._size):
			for x in range(self._size):
				self._modules[y][x] ^= (masker(x, y) == 0) and (not self._isfunction[y][x])
	
	
	def _get_penalty_score(self):
		"""Calculates and returns the penalty score based on state of this QR Code's current modules.
		This is used by the automatic mask choice algorithm to find the mask pattern that yields the lowest score."""
		result = 0
		size = self._size
		modules = self._modules
		
		# Adjacent modules in row having same color, and finder-like patterns
		for y in range(size):
			runcolor = False
			runx = 0
			runhistory = collections.deque([0] * 7, 7)
			for x in range(size):
				if modules[y][x] == runcolor:
					runx += 1
					if runx == 5:
						result += QrCode._PENALTY_N1
					elif runx > 5:
						result += 1
				else:
					self._finder_penalty_add_history(runx, runhistory)
					if not runcolor:
						result += self._finder_penalty_count_patterns(runhistory) * QrCode._PENALTY_N3
					runcolor = modules[y][x]
					runx = 1
			result += self._finder_penalty_terminate_and_count(runcolor, runx, runhistory) * QrCode._PENALTY_N3
		# Adjacent modules in column having same color, and finder-like patterns
		for x in range(size):
			runcolor = False
			runy = 0
			runhistory = collections.deque([0] * 7, 7)
			for y in range(size):
				if modules[y][x] == runcolor:
					runy += 1
					if runy == 5:
						result += QrCode._PENALTY_N1
					elif runy > 5:
						result += 1
				else:
					self._finder_penalty_add_history(runy, runhistory)
					if not runcolor:
						result += self._finder_penalty_count_patterns(runhistory) * QrCode._PENALTY_N3
					runcolor = modules[y][x]
					runy = 1
			result += self._finder_penalty_terminate_and_count(runcolor, runy, runhistory) * QrCode._PENALTY_N3
		
		# 2*2 blocks of modules having same color
		for y in range(size - 1):
			for x in range(size - 1):
				if modules[y][x] == modules[y][x + 1] == modules[y + 1][x] == modules[y + 1][x + 1]:
					result += QrCode._PENALTY_N2
		
		# Balance of dark and light modules
		dark = sum((1 if cell else 0) for row in modules for cell in row)
		total = size**2  # Note that size is odd, so dark/total != 1/2
		# Compute the smallest integer k >= 0 such that (45-5k)% <= dark/total <= (55+5k)%
		k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
		assert 0 <= k <= 9
		result += k * QrCode._PENALTY_N4
		assert 0 <= result <= 2568888  # Non-tight upper bound based on default values of PENALTY_N1, ..., N4
		return result
	
	
	# ---- Private helper functions ----
	
	def _get_alignment_pattern_positions(self):
		"""Returns an ascending list of positions of alignment patterns for this version number.
		Each position is in the range [0,177), and are used on both the x and y axes.
		This could be implemented as lookup table of 40 variable-length lists of integers."""
		if self._version == 1:
			return []
		else:
			numalign = self._version // 7 + 2
			step = (self._version * 8 + numalign * 3 + 5) // (numalign * 4 - 4) * 2
			result = [(self._size - 7 - i * step) for i in range(numalign - 1)] + [6]
			return list(reversed(result))
	
	
	@staticmethod
	def _get_num_raw_data_modules(ver):
		"""Returns the number of data bits that can be stored in a QR Code of the given version number, after
		all function modules are excluded. This includes remainder bits, so it might not be a multiple of 8.
		The result is in the range [208, 29648]. This could be implemented as a 40-entry lookup table."""
		if not (QrCode.MIN_VERSION <= ver <= QrCode.MAX_VERSION):
			raise ValueError("Version number out of range")
		result = (16 * ver + 128) * ver + 64
		if ver >= 2:
			numalign = ver // 7 + 2
			result -= (25 * numalign - 10) * numalign - 55
			if ver >= 7:
				result -= 36
		assert 208 <= result <= 29648
		return result
	
	
	@staticmethod
	def _get_num_data_codewords(ver, ecl):
		"""Returns the number of 8-bit data (i.e. not error correction) codewords contained in any
		QR Code of the given version number and error correction level, with remainder bits discarded.
		This stateless pure function could be implemented as a (40*4)-cell lookup table."""
		return QrCode._get_num_raw_data_modules(ver) // 8 \
			- QrCode._ECC_CODEWORDS_PER_BLOCK    [ecl.ordinal][ver] \
			* QrCode._NUM_ERROR_CORRECTION_BLOCKS[ecl.ordinal][ver]
	
	
	@staticmethod
	def _reed_solomon_compute_divisor(degree):
		"""Returns a Reed-Solomon ECC generator polynomial for the given degree. This could be
		implemented as a lookup table over all possible parameter values, instead of as an algorithm."""
		if not (1 <= degree <= 255):
			raise ValueError("Degree out of range")
		# Polynomial coefficients are stored from highest to lowest power, excluding the leading term which is always 1.
		# For example the polynomial x^3 + 255x^2 + 8x + 93 is stored as the uint8 array [255, 8, 93].
		result = bytearray([0] * (degree - 1) + [1])  # Start off with the monomial x^0
		
		# Compute the product polynomial (x - r^0) * (x - r^1) * (x - r^2) * ... * (x - r^{degree-1}),
		# and drop the highest monomial term which is always 1x^degree.
		# Note that r = 0x02, which is a generator element of this field GF(2^8/0x11D).
		root = 1
		for _ in range(degree):  # Unused variable i
			# Multiply the current product by (x - r^i)
			for j in range(degree):
				result[j] = QrCode._reed_solomon_multiply(result[j], root)
				if j + 1 < degree:
					result[j] ^= result[j + 1]
			root = QrCode._reed_solomon_multiply(root, 0x02)
		return result
	
	
	@staticmethod
	def _reed_solomon_compute_remainder(data, divisor):
		"""Returns the Reed-Solomon error correction codeword for the given data and divisor polynomials."""
		result = bytearray([0] * len(divisor))
		for b in data:  # Polynomial division
			factor = b ^ result.pop(0)
			result.append(0)
			for (i, coef) in enumerate(divisor):
				result[i] ^= QrCode._reed_solomon_multiply(coef, factor)
		return result
	
	
	@staticmethod
	def _reed_solomon_multiply(x, y):
		"""Returns the product of the two given field elements modulo GF(2^8/0x11D). The arguments and result
		are unsigned 8-bit integers. This could be implemented as a lookup table of 256*256 entries of uint8."""
		if (x >> 8 != 0) or (y >> 8 != 0):
			raise ValueError("Byte out of range")
		# Russian peasant multiplication
		z = 0
		for i in reversed(range(8)):
			z = (z << 1) ^ ((z >> 7) * 0x11D)
			z ^= ((y >> i) & 1) * x
		assert z >> 8 == 0
		return z
	
	
	def _finder_penalty_count_patterns(self, runhistory):
		"""Can only be called immediately after a light run is added, and
		returns either 0, 1, or 2. A helper function for _get_penalty_score()."""
		n = runhistory[1]
		assert n <= self._size * 3
		core = n > 0 and (runhistory[2] == runhistory[4] == runhistory[5] == n) and runhistory[3] == n * 3
		return (1 if (core and runhistory[0] >= n * 4 and runhistory[6] >= n) else 0) \
		     + (1 if (core and runhistory[6] >= n * 4 and runhistory[0] >= n) else 0)
	
	
	def _finder_penalty_terminate_and_count(self, currentruncolor, currentrunlength, runhistory):
		"""Must be called at the end of a line (row or column) of modules. A helper function for _get_penalty_score()."""
		if currentruncolor:  # Terminate dark run
			self._finder_penalty_add_history(currentrunlength, runhistory)
			currentrunlength = 0
		currentrunlength += self._size  # Add light border to final run
		self._finder_penalty_add_history(currentrunlength, runhistory)
		return self._finder_penalty_count_patterns(runhistory)
	
	
	def _finder_penalty_add_history(self, currentrunlength, runhistory):
		if runhistory[0] == 0:
			currentrunlength += self._size  # Add light border to initial run
		runhistory.appendleft(currentrunlength)
	
	
	# ---- Constants and tables ----
	
	MIN_VERSION =  1  # The minimum version number supported in the QR Code Model 2 standard
	MAX_VERSION = 40  # The maximum version number supported in the QR Code Model 2 standard
	
	# For use in _get_penalty_score(), when evaluating which mask is best.
	_PENALTY_N1 =  3
	_PENALTY_N2 =  3
	_PENALTY_N3 = 40
	_PENALTY_N4 = 10
	
	_ECC_CODEWORDS_PER_BLOCK = (
		# Version: (note that index 0 is for padding, and is set to an illegal value)
		# 0,  1,  2,  3,  4,  5,  6,  7,  8,  9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40    Error correction level
		(-1,  7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),  # Low
		(-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),  # Medium
		(-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),  # Quartile
		(-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30))  # High
	
	_NUM_ERROR_CORRECTION_BLOCKS = (
		# Version: (note that index 0 is for padding, and is set to an illegal value)
		# 0, 1, 2, 3, 4, 5, 6, 7, 8, 9,10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40    Error correction level
		(-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4,  4,  4,  4,  4,  6,  6,  6,  6,  7,  8,  8,  9,  9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),  # Low
		(-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5,  5,  8,  9,  9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),  # Medium
		(-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8,  8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),  # Quartile
		(-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81))  # High
	
	_MASK_PATTERNS = (
		(lambda x, y:  (x + y) % 2                  ),
		(lambda x, y:  y % 2                        ),
		(lambda x, y:  x % 3                        ),
		(lambda x, y:  (x + y) % 3                  ),
		(lambda x, y:  (x // 3 + y // 2) % 2        ),
		(lambda x, y:  x * y % 2 + x * y % 3        ),
		(lambda x, y:  (x * y % 2 + x * y % 3) % 2  ),
		(lambda x, y:  ((x + y) % 2 + x * y % 3) % 2),
	)
	
	
	# ---- Public helper enumeration ----
	
	class Ecc:
		
		"""The error correction level in a QR Code symbol. Immutable."""
		# Private constructor
		def __init__(self, i, fb):
			self.ordinal = i
			self.formatbits = fb
		
		# Placeholders
	
	# Public constants. Create them outside the class.
	Ecc.LOW      = Ecc(0, 1)  # The QR Code can tolerate about  7% erroneous codewords
	Ecc.MEDIUM   = Ecc(1, 0)  # The QR Code can tolerate about 15% erroneous codewords
	Ecc.QUARTILE = Ecc(2, 3)  # The QR Code can tolerate about 25% erroneous codewords
	Ecc.HIGH     = Ecc(3, 2)  # The QR Code can tolerate about 30% erroneous codewords



# ---- Data segment class ----

class QrSegment:
	"""A segment of character/binary/control data in a QR Code symbol.
	Instances of this class are immutable.
	The mid-level way to create a segment is to take the payload data
	and call a static factory function such as QrSegment.make_numeric().
	The low-level way to create a segment is to custom-make the bit buffer
	and call the QrSegment() constructor with appropriate values.
	This segment class imposes no length restrictions, but QR Codes have restrictions.
	Even in the most favorable conditions, a QR Code can only hold 7089 characters of data.
	Any segment longer than this is meaningless for the purpose of generating QR Codes."""
	
	# ---- Static factory functions (mid level) ----
	
	@staticmethod
	def make_bytes(data):
		"""Returns a segment representing the given binary data encoded in byte mode.
		All input byte lists are acceptable. Any text string can be converted to
		UTF-8 bytes (s.encode("UTF-8")) and encoded as a byte mode segment."""
		bb = _BitBuffer()
		for b in data:
			bb.append_bits(b, 8)
		return QrSegment(QrSegment.Mode.BYTE, len(data), bb)
	
	
	@staticmethod
	def make_numeric(digits):
		"""Returns a segment representing the given string of decimal digits encoded in numeric mode."""
		if not QrSegment.is_numeric(digits):
			raise ValueError("String contains non-numeric characters")
		bb = _BitBuffer()
		i = 0
		while i < len(digits):  # Consume up to 3 digits per iteration
			n = min(len(digits) - i, 3)
			bb.append_bits(int(digits[i : i + n]), n * 3 + 1)
			i += n
		return QrSegment(QrSegment.Mode.NUMERIC, len(digits), bb)
	
	
	@staticmethod
	def make_alphanumeric(text):
		"""Returns a segment representing the given text string encoded in alphanumeric mode.
		The characters allowed are: 0 to 9, A to Z (uppercase only), space,
		dollar, percent, asterisk, plus, hyphen, period, slash, colon."""
		if not QrSegment.is_alphanumeric(text):
			raise ValueError("String contains unencodable characters in alphanumeric mode")
		bb = _BitBuffer()
		for i in range(0, len(text) - 1, 2):  # Process groups of 2
			temp = QrSegment._ALPHANUMERIC_ENCODING_TABLE[text[i]] * 45
			temp += QrSegment._ALPHANUMERIC_ENCODING_TABLE[text[i + 1]]
			bb.append_bits(temp, 11)
		if len(text) % 2 > 0:  # 1 character remaining
			bb.append_bits(QrSegment._ALPHANUMERIC_ENCODING_TABLE[text[-1]], 6)
		return QrSegment(QrSegment.Mode.ALPHANUMERIC, len(text), bb)
	
	
	@staticmethod
	def make_segments(text):
		"""Returns a new mutable list of zero or more segments to represent the given Unicode text string.
		The result may use various segment modes and switch modes to optimize the length of the bit stream."""
		
		# Select the most efficient segment encoding automatically
		if text == "":
			return []
		elif QrSegment.is_numeric(text):
			return [QrSegment.make_numeric(text)]
		elif QrSegment.is_alphanumeric(text):
			return [QrSegment.make_alphanumeric(text)]
		else:
			return [QrSegment.make_bytes(text.encode("UTF-8"))]
	
	
	@staticmethod
	def make_eci(assignval):
		"""Returns a segment representing an Extended Channel Interpretation
		(ECI) designator with the given assignment value."""
		bb = _BitBuffer()
		if assignval < 0:
			raise ValueError("ECI assignment value out of range")
		elif assignval < (1 << 7):
			bb.append_bits(assignval, 8)
		elif assignval < (1 << 14):
			bb.append_bits(0b10, 2)
			bb.append_bits(assignval, 14)
		elif assignval < 1000000:
			bb.append_bits(0b110, 3)
			bb.append_bits(assignval, 21)
		else:
			raise ValueError("ECI assignment value out of range")
		return QrSegment(QrSegment.Mode.ECI, 0, bb)
	
	
	# Tests whether the given string can be encoded as a segment in numeric mode.
	# A string is encodable iff each character is in the range 0 to 9.
	@staticmethod
	def is_numeric(text):
		return QrSegment._NUMERIC_REGEX.fullmatch(text) is not None
	
	
	# Tests whether the given string can be encoded as a segment in alphanumeric mode.
	# A string is encodable iff each character is in the following set: 0 to 9, A to Z
	# (uppercase only), space, dollar, percent, asterisk, plus, hyphen, period, slash, colon.
	@staticmethod
	def is_alphanumeric(text):
		return QrSegment._ALPHANUMERIC_REGEX.fullmatch(text) is not None
	
	
	# ---- Private fields ----
	
	# The mode indicator of this segment. Accessed through get_mode().
	
	# The length of this segment's unencoded data. Measured in characters for
	# numeric/alphanumeric/kanji mode, bytes for byte mode, and 0 for ECI mode.
	# Always zero or positive. Not the same as the data's bit length.
	# Accessed through get_num_chars().
	
	# The data bits of this segment. Accessed through get_data().
	
	
	# ---- Constructor (low level) ----
	
	def __init__(self, mode, numch, bitdata):
		"""Creates a new QR Code segment with the given attributes and data.
		The character count (numch) must agree with the mode and the bit buffer length,
		but the constraint isn't checked. The given bit buffer is cloned and stored."""
		if numch < 0:
			raise ValueError()
		self._mode = mode
		self._numchars = numch
		self._bitdata = list(bitdata)  # Make defensive copy
	
	
	# ---- Accessor methods ----
	
	def get_mode(self):
		"""Returns the mode field of this segment."""
		return self._mode
	
	def get_num_chars(self):
		"""Returns the character count field of this segment."""
		return self._numchars
	
	def get_data(self):
		"""Returns a new copy of the data bits of this segment."""
		return list(self._bitdata)  # Make defensive copy
	
	
	# Package-private function
	@staticmethod
	def get_total_bits(segs, version):
		"""Calculates the number of bits needed to encode the given segments at
		the given version. Returns a non-negative number if successful. Otherwise
		returns None if a segment has too many characters to fit its length field."""
		result = 0
		for seg in segs:
			ccbits = seg.get_mode().num_char_count_bits(version)
			if seg.get_num_chars() >= (1 << ccbits):
				return None  # The segment's length doesn't fit the field's bit width
			result += 4 + ccbits + len(seg._bitdata)
		return result
	
	
	# ---- Constants ----
	
	# Describes precisely all strings that are encodable in numeric mode.
	_NUMERIC_REGEX = re.compile(r"[0-9]*")
	
	# Describes precisely all strings that are encodable in alphanumeric mode.
	_ALPHANUMERIC_REGEX = re.compile(r"[A-Z0-9 $%*+./:-]*")
	
	# Dictionary of "0"->0, "A"->10, "$"->37, etc.
	_ALPHANUMERIC_ENCODING_TABLE = {ch: i for (i, ch) in enumerate("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:")}
	
	
	# ---- Public helper enumeration ----
	
	class Mode:
		"""Describes how a segment's data bits are interpreted. Immutable."""
		
		
		# Private constructor
		def __init__(self, modebits, charcounts):
			self._modebits = modebits
			self._charcounts = charcounts
		
		# Package-private method
		def get_mode_bits(self):
			"""Returns an unsigned 4-bit integer value (range 0 to 15) representing the mode indicator bits for this mode object."""
			return self._modebits
		
		# Package-private method
		def num_char_count_bits(self, ver):
			"""Returns the bit width of the character count field for a segment in this mode
			in a QR Code at the given version number. The result is in the range [0, 16]."""
			return self._charcounts[(ver + 7) // 17]
		
		# Placeholders
	
	# Public constants. Create them outside the class.
	Mode.NUMERIC      = Mode(0x1, (10, 12, 14))
	Mode.ALPHANUMERIC = Mode(0x2, ( 9, 11, 13))
	Mode.BYTE         = Mode(0x4, ( 8, 16, 16))
	Mode.KANJI        = Mode(0x8, ( 8, 10, 12))
	Mode.ECI          = Mode(0x7, ( 0,  0,  0))



# ---- Private helper class ----

class _BitBuffer(list):
	"""An appendable sequence of bits (0s and 1s). Mainly used by QrSegment."""
	
	def append_bits(self, val, n):
		"""Appends the given number of low-order bits of the given
		value to this buffer. Requires n >= 0 and 0 <= val < 2^n."""
		if (n < 0) or (val >> n != 0):
			raise ValueError("Value out of range")
		self.extend(((val >> i) & 1) for i in reversed(range(n)))


def _get_bit(x, i):
	"""Returns true iff the i'th bit of x is set to 1."""
	return (x >> i) & 1 != 0



class DataTooLongError(ValueError):
	"""Raised when the supplied data does not fit any QR Code version. Ways to handle this exception include:
	- Decrease the error correction level if it was greater than Ecc.LOW.
	- If the encode_segments() function was called with a maxversion argument, then increase
	  it if it was less than QrCode.MAX_VERSION. (This advice does not apply to the other
	  factory functions because they search all versions up to QrCode.MAX_VERSION.)
	- Split the text data into better or optimal segments in order to reduce the number of bits required.
	- Change the text or binary data to be shorter.
	- Change the text to fit the character set of a particular segment mode (e.g. alphanumeric).
	- Propagate the error upward to the caller/user."""
	pass


# ==== qrsend ====

import argparse
import hashlib
import html
import json
import os
import pathlib
import struct
import sys
import tempfile
import webbrowser
import zlib

MAGIC = b"QT"
PROTOCOL_VERSION = 1
TYPE_META = 0
TYPE_DATA = 1
HEADER = struct.Struct(">2sBB4sHH")  # magic, version, type, session, index, total：共 12 bytes
META_EVERY = 8  # 每 8 張資料片之前插一張 meta 片
MAX_CHUNK = 2900
ECC_LEVELS = {"L": QrCode.Ecc.LOW, "M": QrCode.Ecc.MEDIUM, "Q": QrCode.Ecc.QUARTILE, "H": QrCode.Ecc.HIGH}


def split_chunks(z, chunk):
    return [z[i:i + chunk] for i in range(0, len(z), chunk)]


def build_frames(data, name, chunk=700, session=None):
    """回傳 (session, meta 片, 資料片清單)，每一片都是一張 QR 的完整內容。"""
    if session is None:
        session = os.urandom(4)
    z = zlib.compress(data, 9)
    chunks = split_chunks(z, chunk)
    total = len(chunks)
    if total > 0xFFFF:
        raise ValueError("file needs {} frames, the limit is 65535".format(total))
    meta = json.dumps({
        "name": name,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "zsize": len(z),
        "chunk": chunk,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8", "replace")
    meta_frame = HEADER.pack(MAGIC, PROTOCOL_VERSION, TYPE_META, session, 0, total) + meta
    data_frames = [HEADER.pack(MAGIC, PROTOCOL_VERSION, TYPE_DATA, session, i, total) + c
                   for i, c in enumerate(chunks)]
    return session, meta_frame, data_frames


def playlist_order(total):
    """輪播順序：None 代表 meta 片，數字代表資料片編號。"""
    order = []
    for i in range(total):
        if i % META_EVERY == 0:
            order.append(None)
        order.append(i)
    return order


def make_qr(frame, ecc="L"):
    return QrCode.encode_binary(frame, ECC_LEVELS[ecc])


def qr_svg_path(qr):
    """SVG path：每一列連續的深色模組合成一個矩形。"""
    size = qr.get_size()
    parts = []
    for y in range(size):
        x = 0
        while x < size:
            if qr.get_module(x, y):
                x0 = x
                while x < size and qr.get_module(x, y):
                    x += 1
                parts.append("M{} {}h{}v1h-{}z".format(x0, y, x - x0, x - x0))
            else:
                x += 1
    return "".join(parts)


def qr_pgm(qr, scale=4, border=4):
    """灰階 PGM（P5）影像，含 border 模組寬的白邊。"""
    size = qr.get_size()
    n = (size + 2 * border) * scale
    white = b"\xff" * n
    rows = []
    for y in range(-border, size + border):
        row = bytearray(white)
        for x in range(size):
            if qr.get_module(x, y):
                s = (x + border) * scale
                row[s:s + scale] = b"\x00" * scale
        rows.extend([bytes(row)] * scale)
    return "P5\n{} {}\n255\n".format(n, n).encode("ascii") + b"".join(rows)


PLAYER_HTML = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>qrsend - __TITLE__</title>
<style>
html, body { margin: 0; height: 100%; background: #fff; color: #222; font-family: sans-serif; }
#wrap { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; }
#qr { height: calc(100vh - 80px); width: calc(100vh - 80px); max-width: 100vw; max-height: 100vw; }
#bar { height: 64px; font-size: 20px; text-align: center; line-height: 1.5; }
#help { font-size: 14px; color: #666; }
</style>
</head>
<body>
<div id="wrap">
<svg id="qr" xmlns="http://www.w3.org/2000/svg" shape-rendering="crispEdges"><rect id="bg" fill="#fff"/><path id="mod" fill="#000"/></svg>
<div id="bar"><span id="info"></span><br><span id="help">空白鍵 暫停/播放　←/→ 上一張/下一張　輸入編號＋Enter 跳到該張　+/- 調速度　F11 全螢幕</span></div>
</div>
<script>
var DATA = __DATA__;
(function () {
  var frames = DATA.frames, total = DATA.total;
  var pos = 0, playing = true, fps = DATA.fps, timer = null, typed = "";
  var svg = document.getElementById("qr"), bg = document.getElementById("bg");
  var mod = document.getElementById("mod"), info = document.getElementById("info");

  function show() {
    var f = frames[pos], n = f.size + 8;
    svg.setAttribute("viewBox", "-4 -4 " + n + " " + n);
    bg.setAttribute("x", "-4");
    bg.setAttribute("y", "-4");
    bg.setAttribute("width", String(n));
    bg.setAttribute("height", String(n));
    mod.setAttribute("d", f.d);
    var label = f.index === null ? "檔頭" : "第 " + (f.index + 1) + " 張 / 共 " + total + " 張";
    info.textContent = DATA.name + "　" + label + "　session " + DATA.session + "　" + fps + " fps" +
      (playing ? "" : "　[暫停]") + (typed ? "　跳到：" + typed : "");
  }

  function schedule() {
    clearTimeout(timer);
    if (playing) {
      timer = setTimeout(function () { pos = (pos + 1) % frames.length; show(); schedule(); }, 1000 / fps);
    }
  }

  function jump(n) {
    for (var i = 0; i < frames.length; i++) {
      if (frames[i].index === n - 1) { pos = i; playing = false; return; }
    }
  }

  document.addEventListener("keydown", function (e) {
    var k = e.key;
    if (k === " " || k === "Spacebar") { playing = !playing; }
    else if (k === "ArrowRight" || k === "Right") { playing = false; pos = (pos + 1) % frames.length; }
    else if (k === "ArrowLeft" || k === "Left") { playing = false; pos = (pos - 1 + frames.length) % frames.length; }
    else if (k === "+" || k === "=") { fps = Math.min(15, fps + 1); }
    else if (k === "-") { fps = Math.max(1, fps - 1); }
    else if (/^[0-9]$/.test(k)) { typed += k; }
    else if (k === "Enter") { if (typed) { jump(parseInt(typed, 10)); } typed = ""; }
    else if (k === "Escape") { typed = ""; }
    else { return; }
    e.preventDefault();
    show();
    schedule();
  });

  show();
  schedule();
})();
</script>
</body>
</html>
"""


def player_html(name, session, meta_qr, data_qrs, fps):
    meta_d = qr_svg_path(meta_qr)
    frames = []
    for i in playlist_order(len(data_qrs)):
        if i is None:
            frames.append({"index": None, "size": meta_qr.get_size(), "d": meta_d})
        else:
            frames.append({"index": i, "size": data_qrs[i].get_size(), "d": qr_svg_path(data_qrs[i])})
    data = {"name": name, "session": session.hex(), "total": len(data_qrs), "fps": fps, "frames": frames}
    data_js = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c")
    return PLAYER_HTML.replace("__TITLE__", html.escape(name)).replace("__DATA__", data_js)


def say(text):
    """終端機不支援的字元（例如 C locale 下的中文檔名）改用跳脫碼顯示，不讓程式中斷。"""
    enc = sys.stdout.encoding or "utf-8"
    sys.stdout.write(text.encode(enc, "backslashreplace").decode(enc) + "\n")


def main(argv=None):
    p = argparse.ArgumentParser(description="Turn a file into a looping sequence of QR codes shown in a browser.")
    p.add_argument("file")
    p.add_argument("--chunk", type=int, default=700, help="compressed bytes per QR code (default 700)")
    p.add_argument("--fps", type=int, default=5, help="frames per second, 1-15 (default 5)")
    p.add_argument("--ecc", choices=["L", "M", "Q", "H"], default="L", help="QR error correction level (default L)")
    p.add_argument("--out", help="player page path (default: system temp folder)")
    p.add_argument("--no-open", action="store_true", help="do not open the browser")
    p.add_argument("--export-frames", metavar="DIR", help="write each QR code as a PGM image instead (for testing)")
    a = p.parse_args(argv)
    if not 1 <= a.chunk <= MAX_CHUNK:
        p.error("--chunk must be between 1 and {}".format(MAX_CHUNK))
    if not 1 <= a.fps <= 15:
        p.error("--fps must be between 1 and 15")

    with open(a.file, "rb") as f:
        data = f.read()
    name = os.path.basename(a.file)
    try:
        session, meta_frame, data_frames = build_frames(data, name, a.chunk)
    except ValueError as e:
        p.error(str(e))
    try:
        meta_qr = make_qr(meta_frame, a.ecc)
        data_qrs = [make_qr(f, a.ecc) for f in data_frames]
    except DataTooLongError:
        p.error("a frame does not fit in one QR code; use a smaller --chunk or a lower --ecc")

    say("file      {} ({} bytes, {} bytes compressed)".format(name, len(data), sum(len(f) - HEADER.size for f in data_frames)))
    say("frames    {} (QR version up to {})".format(len(data_qrs), max(q.get_version() for q in data_qrs)))
    say("session   {}".format(session.hex()))
    say("sha256    {}".format(hashlib.sha256(data).hexdigest()))

    if a.export_frames:
        os.makedirs(a.export_frames, exist_ok=True)
        with open(os.path.join(a.export_frames, "meta.pgm"), "wb") as f:
            f.write(qr_pgm(meta_qr))
        for i, q in enumerate(data_qrs):
            with open(os.path.join(a.export_frames, "data_{:05d}.pgm".format(i)), "wb") as f:
                f.write(qr_pgm(q))
        say("exported  {} images to {}".format(len(data_qrs) + 1, a.export_frames))
        return

    out = a.out or os.path.join(tempfile.gettempdir(), "qrsend_{}.html".format(session.hex()))
    with open(out, "w", encoding="utf-8", errors="replace") as f:
        f.write(player_html(name, session, meta_qr, data_qrs, a.fps))
    say("player    {}".format(out))
    if not a.no_open:
        webbrowser.open(pathlib.Path(out).resolve().as_uri())


if __name__ == "__main__":
    main()
