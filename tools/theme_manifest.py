"""Pinned, unchanged upstream theme packages for this recipe."""
DEFAULT_THEME = 'footstrap'
THEMES = {
    'argon': {
        'package': 'luci-theme-argon', 'version': '3.1.0',
        'file': 'luci-theme-argon_3.1.0_all.ipk',
        'sha256': '837337a26bb3d51d9634398de30ba9d64d238476d4d0834d472ade2cf97fd826',
        'source': 'https://github.com/jerrykuku/luci-theme-argon/releases/tag/v3.1.0',
    },
    'footstrap': {
        'package': 'luci-theme-footstrap', 'version': '0.14.16-r1',
        'file': 'luci-theme-footstrap_0.14.16-r1_all.ipk',
        'sha256': 'f7a1cbd6f05dd884a822623b9d92349ba033643cfa600139c134eeca8797c997',
        'source': 'https://github.com/VizzleTF/luci-theme-footstrap/releases/tag/v0.14.16',
        'source_commit': '4523e6e998652523b291627819381ccb0867e371',
    },
}
