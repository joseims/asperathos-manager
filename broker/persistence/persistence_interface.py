# Copyright (c) 2019 UFCG-LSD.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import abc
import six


def required(fun):
    return abc.abstractmethod(fun)


@six.add_metaclass(abc.ABCMeta)
class PersistenceInterface(object):
    
    @required
    def put(self, app_id, state):
        pass
    @required
    def get(self, app_id):
        pass
    @required
    def delete(self, app_id):
        pass
    @required
    def delete_all(self, prefix='kj-'): 
        pass
    @required
    def get_all(self, prefix="kj-"):
        pass

