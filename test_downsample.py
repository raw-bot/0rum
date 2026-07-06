import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path('/Applications/0rum/.sandbox/0rum-one-shot-home/0rum-trading')))
from orum.dashboard import _price_series
print(len(_price_series()))
