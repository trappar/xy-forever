"""The simulator's seed of a stock OP-XY just after Track 5 wrote XY Forever (#178; xy_forever/sim.py)."""
from pathlib import Path

from . import paths

STOCK_UPGRADE = 'upgrade/firmware/blackfin/bin/os.ldr'     # the stock updater's own os.ldr (#178: the device adopts it)


def seed_fresh(root, stock='stock', image=None):
    """A stock OP-XY just after Track 5 wrote XY Forever (#178): no xy-forever/ library, the stock updater's
    os.ldr at STOCK_UPGRADE (stock: the stock 1.2.1 image; wrong: the same size, one byte changed; missing: none),
    content/ with a stand-in op1_factory.json. Returns root."""
    root = Path(root)
    image = image if image is not None else (paths.STOCK_121 / 'os.ldr').read_bytes()
    root.mkdir(parents=True, exist_ok=True)
    if stock != 'missing':
        target = root / STOCK_UPGRADE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image if stock == 'stock' else image[:-1] + bytes([image[-1] ^ 1]))
    (root / 'firmware.version').write_text('1.2.1\n')
    content = root / 'content'
    content.mkdir(parents=True, exist_ok=True)
    (content / 'op1_factory.json').write_text('{"xyf_sim": "stand-in op1_factory.json"}\n')
    return root
