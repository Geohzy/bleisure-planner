"""核心函数单元测试：geo / transport / planner / flyai_client。

纯标准库 unittest，不调真实网络或 API（search_poi_multi 用假 search_poi 替换）。
运行：python -m unittest discover tests -v
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

# 让 `unittest discover tests` 也能 import 到项目根目录下的业务模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flyai_client
import geo
import transport
from planner import free_windows_from_meetings

# 纬度 1 度 ≈ 111.195 km，用来按公里数构造南北向的坐标点
KM_PER_LAT_DEG = 111.19492664455873


def _north(lat, lng, km):
    """在 (lat, lng) 正北 km 公里处取一点，便于按目标距离构造用例。"""
    return lat + km / KM_PER_LAT_DEG, lng


class TestHaversine(unittest.TestCase):
    def test_beijing_shanghai_about_1067km(self):
        d = geo.haversine_km(39.9042, 116.4074, 31.2304, 121.4737)
        self.assertGreaterEqual(d, 1060)
        self.assertLessEqual(d, 1080)

    def test_same_point_is_zero(self):
        self.assertEqual(geo.haversine_km(39.9042, 116.4074, 39.9042, 116.4074), 0)

    def test_symmetric(self):
        self.assertAlmostEqual(
            geo.haversine_km(39.9042, 116.4074, 31.2304, 121.4737),
            geo.haversine_km(31.2304, 121.4737, 39.9042, 116.4074),
            places=6,
        )


class TestTransport(unittest.TestCase):
    def test_choose_mode_by_distance(self):
        self.assertEqual(transport.choose_mode(1.0)[0], "步行")
        self.assertEqual(transport.choose_mode(5.0)[0], "地铁/公交")
        self.assertEqual(transport.choose_mode(20.0)[0], "打车")

    def test_estimate_leg_positive_minutes(self):
        lat, lng = 30.0, 120.0
        for km, expect_mode in ((1, "步行"), (5, "地铁/公交"), (20, "打车")):
            miles_lat, miles_lng = _north(lat, lng, km)
            minutes, mode = transport.estimate_leg(lat, lng, miles_lat, miles_lng)
            self.assertEqual(mode, expect_mode, f"{km}km 应选{expect_mode}")
            self.assertIsInstance(minutes, int)
            self.assertGreater(minutes, 0)

    def test_estimate_leg_same_point_no_cost(self):
        self.assertEqual(transport.estimate_leg(30.0, 120.0, 30.0, 120.0), (0, "步行"))


class TestFreeWindows(unittest.TestCase):
    def test_two_meetings_split_into_three_windows(self):
        windows = free_windows_from_meetings(
            [("10:00", "11:00"), ("14:00", "15:00")],
            day_start="09:00", day_end="18:00",
        )
        self.assertEqual(windows, [("09:00", "10:00"), ("11:00", "14:00"), ("15:00", "18:00")])

    def test_windows_not_overlapping(self):
        windows = free_windows_from_meetings(
            [("10:00", "11:00"), ("14:00", "15:00")],
            day_start="09:00", day_end="18:00",
        )
        for (_, prev_end), (next_start, _) in zip(windows, windows[1:]):
            self.assertLessEqual(prev_end, next_start)

    def test_overlapping_meetings_are_merged(self):
        windows = free_windows_from_meetings(
            [("10:00", "11:30"), ("11:00", "12:00")],
            day_start="09:00", day_end="18:00",
        )
        self.assertEqual(windows, [("09:00", "10:00"), ("12:00", "18:00")])

    def test_tiny_gap_is_dropped(self):
        # 11:00–11:30 只有 30 分钟，低于 min_gap=45，不作为窗口
        windows = free_windows_from_meetings(
            [("10:00", "11:00"), ("11:30", "13:00")],
            day_start="09:00", day_end="18:00",
        )
        self.assertEqual(windows, [("09:00", "10:00"), ("13:00", "18:00")])


def _poi(name, url="https://example.com/x"):
    return {
        "name": name, "address": "", "lat": 30.0, "lng": 120.0,
        "pic": None, "url": url, "free": "UNKNOWN", "desc": "",
    }


class TestSearchPoiMulti(unittest.TestCase):
    """用假 search_poi 替换网络调用，验证去重与单路容错。"""

    def test_dedup_keeps_first_occurrence(self):
        def fake_search_poi(city, category=None, keyword=None):
            if category == "人文古迹":
                return [_poi("同名点"), _poi("独占A")]
            if keyword == "夜市":
                return [_poi("同名点"), _poi("独占B")]
            return []

        with mock.patch.object(flyai_client, "search_poi", side_effect=fake_search_poi):
            out = flyai_client.search_poi_multi("杭州", ["人文古迹", "kw:夜市"])

        self.assertEqual([p["name"] for p in out], ["同名点", "独占A", "独占B"])
        # 同名点只保留第一次出现的那条，来源记为第一个查询
        self.assertEqual(out[0]["src"], "人文古迹")

    def test_nameless_poi_is_dropped(self):
        with mock.patch.object(flyai_client, "search_poi",
                               return_value=[_poi(""), _poi(None), _poi("有点")]):
            out = flyai_client.search_poi_multi("杭州", ["博物馆"])
        self.assertEqual([p["name"] for p in out], ["有点"])

    def test_one_query_failure_does_not_break_the_rest(self):
        def fake_search_poi(city, category=None, keyword=None):
            if category == "地标建筑":
                raise RuntimeError("模拟单路失败")
            return [_poi("好点")]

        buf = io.StringIO()
        with mock.patch.object(flyai_client, "search_poi", side_effect=fake_search_poi):
            with redirect_stdout(buf):
                out = flyai_client.search_poi_multi("杭州", ["地标建筑", "博物馆"])

        self.assertEqual([p["name"] for p in out], ["好点"])
        self.assertIn("已跳过", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
