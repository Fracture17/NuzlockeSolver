# Python mGBA Bindings — Usage & Audio Guide

## Setup

mGBA Python bindings are a **custom CFFI build**, not pip-installable. They are
loaded via a `.pth` file that points at the compiled directory:

```
/home/Fracture/mgba/build/python/lib.linux-x86_64-cpython-314/
```

The Python package is `mgba`. Its low-level C symbols are exposed through
`from mgba._pylib import ffi, lib`. Everything you need lives in either the
high-level Python wrappers (`mgba.core`, `mgba.image`, etc.) or directly in
`lib` via CFFI.

---

## Core Lifecycle

```python
from mgba._pylib import ffi, lib
import mgba.core as mgba_core
import mgba.image
from mgba.vfs import open_path as vfs_open_path

core = mgba_core.load_path(rom_path)          # returns GBA subclass of Core
save_vf = vfs_open_path(save_path, "r+")
core.load_save(save_vf)

width, height = core.desired_video_dimensions()
image = mgba.image.Image(width, height)
core.set_video_buffer(image)

core.reset()           # must call before run_frame / memory access
```

`core._core` is the raw CFFI `struct mCore*` — use it to call C functions that
the Python wrappers don't expose.

### Per-frame loop

```python
core.run_frame()                              # advances emulation by one frame
core.set_keys(raw=bitmask)                    # GBA buttons: A=1 B=2 Sel=4 Start=8
                                              # Right=16 Left=32 Up=64 Down=128 R=256 L=512
```

### Memory access

```python
core.memory.u32[addr]   # read 32-bit word (returns int-like CFFI value)
core.memory.u16[addr]   # read 16-bit halfword
core.memory.u8[addr]    # read 8-bit byte
core.memory.u32[addr] = value   # write
```

All addresses are GBA bus addresses (e.g. EWRAM starts at 0x02000000).

### Save states

```python
buf = core.save_raw_state()              # returns ffi buffer or None
with open(path, "wb") as f:
    f.write(bytes(ffi.buffer(buf)))

data = open(path, "rb").read()
buf  = ffi.new("unsigned char[]", data)
core.load_raw_state(buf)                 # returns True on success
```

### mGBA C library noise

mGBA's C code logs to **fd 1 (stdout)**. To silence it while keeping Python
`print()` working:

```python
import os, sys
saved = os.dup(1)
devnull = os.open(os.devnull, os.O_WRONLY)
os.dup2(devnull, 1)
os.close(devnull)
sys.stdout = os.fdopen(saved, "w", buffering=1)
```

Do this once, before the first `run_frame`. **Never redirect stdout in a
subprocess shell command** (`> file 2>&1`) while mGBA is running — the C
logger will crash (SIGSEGV) against the redirected fd.

---

## Audio

### Key facts (verified on this machine)

| Parameter | Value | Notes |
|-----------|-------|-------|
| mGBA default synthesis rate | **65,536 Hz** | 2× GBA native (32,768 Hz); not configurable without `mCoreLoadConfig` |
| PulseAudio server rate | **48,000 Hz** | reported by `pactl info`; `sd.query_devices()` lies (returns 44,100) |
| Channels | **2 (stereo)** | always for GBA |
| Sample format | **int16** | signed 16-bit |

Do **not** use `sounddevice`'s `query_devices()` to pick the output rate — it
returns the PortAudio default (44,100 Hz here) which PulseAudio then
pitch-shifts to 48,000 Hz internally. Always hard-code (or `pactl`-query) the
real server rate.

### Audio buffer API (CFFI)

```python
audio_buf = core._core.getAudioBuffer(core._core)   # struct mAudioBuffer*

available = lib.mAudioBufferAvailable(audio_buf)    # stereo frames ready
# allocate: available frames × 2 channels × 2 bytes = available*4 bytes
raw = ffi.new(f"int16_t[{available * 2}]")
read = lib.mAudioBufferRead(audio_buf, raw, available)   # returns frames read
# raw now contains read*2 int16 values interleaved as L,R,L,R,...

lib.mAudioBufferClear(audio_buf)    # discard without reading
```

`mAudioBufferAvailable` and `mAudioBufferRead` both count in **stereo frames**
(one frame = one L/R pair), not individual samples. Reading `count` frames
writes `count × 4` bytes.

### Full audio implementation (the working approach)

```python
import queue, sounddevice as sd
import numpy as np

_gba_rate = 65536   # mGBA default synthesis rate
_out_rate = 48000   # PulseAudio native rate

def _resample(chunk):
    """Linear-interpolation resample (N,2) int16 array from GBA to device rate."""
    if _gba_rate == _out_rate or len(chunk) == 0:
        return chunk
    n_out = max(1, round(len(chunk) * _out_rate / _gba_rate))
    x_old = np.arange(len(chunk), dtype=np.float64)
    x_new = np.linspace(0.0, len(chunk) - 1, n_out)
    result = np.empty((n_out, 2), dtype=np.int16)
    for ch in range(2):
        result[:, ch] = np.clip(
            np.interp(x_new, x_old, chunk[:, ch].astype(np.float64)),
            -32768, 32767,
        ).astype(np.int16)
    return result

audio_queue = queue.Queue(maxsize=8)   # ~130 ms max buffer at 48 kHz
_leftover   = [np.zeros((0, 2), dtype=np.int16)]

def _audio_cb(outdata, frames, time_info, status):
    out = np.zeros((frames, 2), dtype=np.int16)
    pos = 0
    buf = _leftover[0]
    if len(buf):
        n = min(len(buf), frames)
        out[:n] = buf[:n]
        _leftover[0] = buf[n:]
        pos = n
    while pos < frames:
        try:
            chunk = audio_queue.get_nowait()
            n = min(len(chunk), frames - pos)
            out[pos:pos + n] = chunk[:n]
            _leftover[0] = chunk[n:] if n < len(chunk) else np.zeros((0, 2), dtype=np.int16)
            pos += n
        except queue.Empty:
            break   # fill rest with silence
    outdata[:] = out

audio_stream = sd.OutputStream(
    samplerate=_out_rate, channels=2, dtype="int16",
    blocksize=512, callback=_audio_cb,
)
audio_stream.start()
```

**Inside the emulator loop** (must hold `emu_lock` while accessing core):

```python
with emu_lock:
    core.run_frame()
    # ... other per-frame work ...
    audio_buf = core._core.getAudioBuffer(core._core)
    available  = lib.mAudioBufferAvailable(audio_buf)
    if available > 0:
        if fast_forward:
            lib.mAudioBufferClear(audio_buf)
        else:
            raw  = ffi.new(f"int16_t[{available * 2}]")
            read = lib.mAudioBufferRead(audio_buf, raw, available)
            if read > 0:
                chunk = np.frombuffer(
                    ffi.buffer(raw, read * 2 * 2), dtype=np.int16
                ).reshape(read, 2).copy()   # .copy() is essential — ffi buffer is freed on scope exit
                chunk = _resample(chunk)
                try:
                    audio_queue.put_nowait(chunk)
                except queue.Full:
                    pass   # drop silently; brief gap is better than growing delay
```

**On exit:**

```python
audio_stream.stop()
audio_stream.close()
```

### Common mistakes

| Mistake | Symptom | Fix |
|---------|---------|-----|
| Using 32,768 Hz as GBA rate | Pitch shifted (≈1 octave wrong) | Use 65,536 Hz |
| Using `sd.query_devices()` for output rate | Pitch shifted up ~8.8% | Hard-code 48,000 Hz (check with `pactl info`) |
| Forgetting `.copy()` on the numpy array | Garbage audio / crash | Always `.copy()` before queuing |
| Counting samples instead of frames | 2× wrong buffer size | `mAudioBufferAvailable` returns stereo *frames* |
| Large queue (maxsize=32+) | Multi-second audio delay | Use maxsize=6–8 |
| Not clearing buffer during fast-forward | Audio burst when returning to normal speed | Call `mAudioBufferClear` on every fast-forward frame |
