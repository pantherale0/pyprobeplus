import re

import setuptools

with open('README.md', 'r', encoding='utf8') as readme_file:
    long_description = readme_file.read()

# Inspiration: https://stackoverflow.com/a/7071358/6064135
with open('pyprobeplus/__init__.py', 'r', encoding='utf8') as version_file:
    version_groups = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]", version_file.read(), re.M)
    if version_groups:
        version = version_groups.group(1)
    else:
        raise RuntimeError('Unable to find version string!')

REQUIREMENTS = [
    'bleak',
]

DEV_REQUIREMENTS = [
    'bandit >= 1.7,< 1.9',
    'black >= 23,< 26',
    'build >= 0.10,< 1.3',
    'flake8 >= 6,< 8',
    'isort >= 5,< 7',
    'mypy >= 1.5,< 1.16',
    'pytest >= 7,< 9',
    'pytest-cov >= 4,< 7',
    'twine >= 4,< 7',
]

setuptools.setup(
    name='pyprobeplus',
    version=version,
    description='A generic library to interact with a Probe Plus BLE device',
    long_description=long_description,
    long_description_content_type="text/markdown",
    url='http://github.com/pantherale0/pyprobeplus',
    author='pantherale0',
    license='Apache-2.0',
    packages=setuptools.find_packages(),
    package_data={
        'pyprobeplus': [
            'py.typed',
        ]
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
    ],
    install_requires=REQUIREMENTS,
    extras_require={
        'dev': DEV_REQUIREMENTS,
    },
    entry_points={
        'console_scripts': [
            'pyprobeplus=pyprobeplus:main',
        ]
    },
    python_requires='>=3.8, <4',
)
