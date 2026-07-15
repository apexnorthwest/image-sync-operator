#!/usr/bin/env python3
"""
Entrypoint for the image sync operator. Primarily, this initializes the operator and runs the main loop.

Copyright 2026 Apex Northwest

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import logging
import sys
from imagesync.operator import Operator

__author__ = 'Tyler Bevan <tyler@apexnorthwest.com>'
__version__ = '0.1.0'
__license__ = 'Apache 2.0'

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

if __name__ == '__main__':
    operator = Operator()
    operator.run()
