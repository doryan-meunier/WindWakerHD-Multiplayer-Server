"""Exception commune aux accès mémoire de winmem (Windows) et linmem (Linux) : un ``except MemoryReadError`` de
wwlib.cemu attrape les deux (les tests de linmem tournent aussi sous Windows)."""


class MemoryReadError(OSError):
    pass
