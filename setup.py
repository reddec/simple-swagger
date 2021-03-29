from os import getenv

import setuptools

version = getenv('GITHUB_REF', getenv('VERSION', 'dev')).split('/')[-1].strip('v')

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(name="simple-swagger",
                 version=version,
                 author="Aleksandr Baryshnikov",
                 author_email="owner@reddec.net",
                 description="Simple swagger generator for Go",
                 license='MIT',
                 long_description=long_description,
                 long_description_content_type="text/markdown",
                 include_package_data=True,
                 package_data={
                     'simpleswagger': ['templates']
                 },
                 entry_points={
                     'console_scripts': [
                         'simple-swagger = simpleswagger.generator:main',
                     ],
                 },
                 url="https://github.com/reddec/simple-swagger",
                 packages=['simpleswagger'],
                 classifiers=[
                     "Programming Language :: Python :: 3.6",
                     "License :: OSI Approved :: MIT License",
                     "Operating System :: OS Independent",
                     "Intended Audience :: Developers",
                     "Intended Audience :: System Administrators"
                 ],
                 python_requires='>=3.4',
                 install_requires=[
                     'Jinja2~=2.11.3',
                     'PyYAML~=5.4.1'
                 ])
