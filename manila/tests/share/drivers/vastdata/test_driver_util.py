# Copyright 2020 VAST Data Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
import json
import pickle

from ddt import data
from ddt import ddt
from ddt import unpack
from manila.share.drivers.vastdata.driver_util import Bunch
from manila.share.drivers.vastdata.driver_util import bunchify
from manila.share.drivers.vastdata.driver_util import generate_ip_range
from manila.share.drivers.vastdata.driver_util import unbunchify
from manila import test


@ddt
class TestBunch(test.TestCase):
    def setUp(self):
        super(TestBunch, self).setUp()
        self.bunch = Bunch(a=1, b=2)

    def test_bunch_getattr(self):
        self.assertEqual(self.bunch.a, 1)

    def test_bunch_setattr(self):
        self.bunch.c = 3
        self.assertEqual(self.bunch.c, 3)

    def test_bunch_delattr(self):
        del self.bunch.a
        self.assertRaises(AttributeError, lambda: self.bunch.a)

    def test_bunch_to_dict(self):
        self.assertEqual(self.bunch.to_dict(), {"a": 1, "b": 2})

    def test_bunch_from_dict(self):
        self.assertEqual(Bunch.from_dict({"a": 1, "b": 2}), self.bunch)

    def test_bunch_to_json(self):
        self.assertEqual(self.bunch.to_json(), json.dumps({"a": 1, "b": 2}))

    def test_bunch_without(self):
        self.assertEqual(self.bunch.without("a"), Bunch(b=2))

    def test_bunch_but_with(self):
        self.assertEqual(self.bunch.but_with(c=3), Bunch(a=1, b=2, c=3))

    def test_bunch_delattr_missing(self):
        self.assertRaises(
            AttributeError,
            lambda: self.bunch.__delattr__("non_existing_attribute")
        )

    def test_bunch_from_json(self):
        json_bunch = json.dumps({"a": 1, "b": 2})
        self.assertEqual(Bunch.from_json(json_bunch), self.bunch)

    def test_bunch_render(self):
        self.assertEqual(self.bunch.render(), "a=1, b=2")

    def test_bunch_pickle(self):
        pickled_bunch = pickle.dumps(self.bunch)
        unpickled_bunch = pickle.loads(pickled_bunch)
        self.assertEqual(self.bunch, unpickled_bunch)

    @data(True, False)
    def test_bunch_copy(self, deep):
        copy_bunch = self.bunch.copy(deep=deep)
        self.assertEqual(copy_bunch, self.bunch)
        self.assertIsNot(copy_bunch, self.bunch)

    def test_name_starts_with_underscore_and_digit(self):
        bunch = Bunch()
        bunch["1"] = "value"
        self.assertEqual(bunch._1, "value")

    def test_bunch_recursion(self):
        x = Bunch(a="a", b="b", d=Bunch(x="axe", y="why"))
        x.d.x = x
        x.d.y = x.b
        print(x)

    def test_bunch_repr(self):
        self.assertEqual(repr(self.bunch), "Bunch(a=1, b=2)")

    def test_getitem_with_integral_key(self):
        self.bunch["1"] = "value"
        self.assertEqual(self.bunch[1], "value")

    def test_bunch_dir(self):
        self.assertEqual(
            set(i for i in dir(self.bunch) if not i.startswith("_")),
            {
                "a",
                "b",
                "but_with",
                "clear",
                "copy",
                "from_dict",
                "from_json",
                "fromkeys",
                "get",
                "items",
                "keys",
                "pop",
                "popitem",
                "render",
                "setdefault",
                "to_dict",
                "to_json",
                "update",
                "values",
                "without",
            },
        )


class TestBunchify(test.TestCase):
    def test_bunchify(self):
        self.assertEqual(bunchify({"a": 1, "b": 2}, c=3), Bunch(a=1, b=2, c=3))
        x = bunchify(dict(a=[dict(b=5), 9, (1, 2)], c=8))
        self.assertEqual(x.a[0].b, 5)
        self.assertEqual(x.a[1], 9)
        self.assertIsInstance(x.a[2], tuple)
        self.assertEqual(x.c, 8)
        self.assertEqual(x.pop("c"), 8)


class TestUnbunchify(test.TestCase):
    def test_unbunchify(self):
        self.assertEqual(unbunchify(Bunch(a=1, b=2)), {"a": 1, "b": 2})


@ddt
class TestGenerateIpRange(test.TestCase):

    @data(
        (
            [["15.0.0.1", "15.0.0.4"], ["10.0.0.27", "10.0.0.30"]],
            [
                "15.0.0.1",
                "15.0.0.2",
                "15.0.0.3",
                "15.0.0.4",
                "10.0.0.27",
                "10.0.0.28",
                "10.0.0.29",
                "10.0.0.30",
            ],
        ),
        (
            [["15.0.0.1", "15.0.0.1"], ["10.0.0.20", "10.0.0.20"]],
            ["15.0.0.1", "10.0.0.20"],
        ),
        ([], []),
    )
    @unpack
    def test_generate_ip_range(self, ip_ranges, expected):
        ips = generate_ip_range(ip_ranges)
        assert ips == expected
