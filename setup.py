#!/usr/bin/env python
from setuptools import setup, find_packages
from imp import load_source
from os import path
import io

__version__ = load_source('satstac.landsat.version', 'satstac/landsat/version.py').__version__

here = path.abspath(path.dirname(__file__))

# get the dependencies and installs
with io.open(path.join(here, 'requirements.txt'), encoding='utf-8') as f:
    all_reqs = f.read().split('\n')

install_requires = [x.strip() for x in all_reqs if 'git+' not in x]
dependency_links = [x.strip().replace('git+', '') for x in all_reqs if 'git+' not in x]

setup(
    name='sat-stac-landsat',
    author='Matthew Hanson (matthewhanson)',
    author_email='matt.a.hanson@gmail.com',
    version=__version__,
    description='Creates STAC catalog for Landsat-8',
    url='https://github.com/sat-utils/sat-stac-landsat.git',
    license='MIT',
    classifiers=[
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3.6',
    ],
    keywords='',
    entry_points={
        'console_scripts': ['sat-stac-landsat=satstac.landsat.cli:cli'],
    },
    packages=['satstac.landsat'],
    include_package_data=True,
    install_requires=install_requires,
    dependency_links=dependency_links,
)
