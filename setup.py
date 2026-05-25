from setuptools import setup, find_packages

VERSION = "0.0.1"

setup(
    name="p2p-chat",
    version=VERSION,
    description="Distributed peer-to-peer chat system with tracker discovery.",
    author="P2P Chat Project",
    packages=find_packages(),
    entry_points={"console_scripts": ["p2p_demo=p2p_chat.main:main"]},
    python_requires=">=3.9",
    extras_require={
        "crypto": ["cryptography>=41"],
    },
)
