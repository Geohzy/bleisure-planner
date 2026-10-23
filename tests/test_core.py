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
from planner import (free_windows_from_meetings, plan, swap_stop,
                     stop_alternatives, apply_buffer, resolve_open_window,
                     open_hours_label, _fits_open)
from llm import judge_verdict

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


def _poi(name, url="https://example.com/x", lat=30.0, lng=120.0,
         category=None, open_hours=None, src=None, desc=""):
    return {
        "name": name, "address": "", "lat": lat, "lng": lng,
        "pic": None, "url": url, "free": "UNKNOWN", "desc": desc,
        "category": category, "open_hours": open_hours, "src": src,
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


class TestStructuredNotes(unittest.TestCase):
    """F2：notes 从字符串列表升级为结构化 dict，保留 text 字段向后兼容。"""

    def test_plan_notes_are_structured_dicts(self):
        # 一个 30 分钟碎片窗口 → 降级 1 生成结构化 note
        meetings = [("09:30", "12:00"), ("14:00", "17:30")]
        # 手动构造：free_windows 会滤掉 <45 分钟碎片，因此直接测 plan 的窗口入参
        hotel = {"name": "园", "lat": 30.0, "lng": 120.0}
        pois = [_poi("近处A", lat=30.0, lng=120.0), _poi("近处B", lat=30.0, lng=120.0)]
        stops, notes = plan(pois, hotel, [("18:00", "18:30")], visit_minutes=90)
        # 30 分钟碎片 → 降级 1 直接 continue，notes 至少一条且为 dict
        self.assertTrue(notes, "碎片窗口应产生降级说明")
        for n in notes:
            self.assertIsInstance(n, dict)
            for key in ("constraint", "expected", "actual", "action", "text"):
                self.assertIn(key, n, f"note 缺字段 {key}")
            self.assertTrue(n["text"], "text 字段必须人类可读、非空")
            self.assertTrue(n["action"], "action 必须给可操作建议")

    def test_old_code_path_reads_text_field(self):
        # 老消费者只读 text 字段也不报错（向后兼容）
        hotel = {"name": "园", "lat": 30.0, "lng": 120.0}
        pois = [_poi("近处A", lat=30.0, lng=120.0)]
        _, notes = plan(pois, hotel, [("18:00", "18:30")], visit_minutes=90)
        for n in notes:
            self.assertIsInstance(n.get("text", ""), str)
            self.assertTrue(n.get("text"))


class TestSwapStop(unittest.TestCase):
    """F3：本地一键换站（不重新调 flyai），无备选时不得编造 POI。"""

    def _fixture(self):
        lat, lng = 30.0, 120.0
        hotel = {"name": "园", "lat": lat, "lng": lng}
        # 站点 POI 与备选 POI 同池，距离越近排越前
        pois = [
            _poi("站A", lat=_north(lat, lng, 0.5)[0], lng=lng),
            _poi("站B", lat=_north(lat, lng, 1.0)[0], lng=lng),
            _poi("备C", lat=_north(lat, lng, 1.5)[0], lng=lng),
            _poi("备D", lat=_north(lat, lng, 2.0)[0], lng=lng),
        ]
        stops, _ = plan(pois, hotel, [("18:00", "22:00")], visit_minutes=90)
        self.assertEqual(len(stops), 2, "4 小时窗口应排 2 站：A、B")
        return stops, pois, hotel

    def test_swap_replaces_target_stop_only(self):
        stops, pois, hotel = self._fixture()
        before = list(stops)
        new_stops, note = swap_stop(stops, 0, pois, hotel)
        self.assertIsNone(note, "应有备选可换，note 应为 None")
        self.assertEqual(len(new_stops), len(stops))
        # 第 0 站换成了未用过的备选（备C 或备D），最可能是更近的 备C
        names = [s["poi"]["name"] for s in new_stops]
        self.assertNotEqual(names[0], "站A")
        self.assertIn(names[0], {"备C", "备D"})
        # 其它站保持不变（第 1 站仍是 站B）
        self.assertEqual(names[1], "站B")
        # 换站后该站交通字段重算、时刻不变
        self.assertNotEqual(new_stops[0]["start"], "")
        self.assertEqual(new_stops[0]["start"], before[0]["start"])
        self.assertEqual(new_stops[0]["end"], before[0]["end"])

    def test_swap_with_no_candidate_returns_original_and_note(self):
        stops, pois, hotel = self._fixture()
        # 把池子缩到只剩当前两站（已全部用掉）→ 无合适备选
        tiny_pool = [_poi(s["poi"]["name"], lat=s["poi"]["lat"], lng=s["poi"]["lng"])
                     for s in stops]
        new_stops, note = swap_stop(stops, 1, tiny_pool, hotel)
        self.assertTrue(note, "无备选时必须返回可读提示")
        self.assertEqual([s["poi"]["name"] for s in new_stops],
                         [s["poi"]["name"] for s in stops], "无备选不得改变原行程")
        # 不得编造 POI：返回的站点名必须仍来自原 stops
        self.assertTrue(all("POI" not in s["poi"]["name"] for s in new_stops))


class TestJudgeVerdictTemplate(unittest.TestCase):
    """F1：无 Key 时裁决走离线模板，必须引用真实数字、不编造。"""

    def test_template_verdict_contains_real_numbers(self):
        stop = {
            "start": "18:00", "end": "19:30", "dist_km": 1.3,
            "leg_min": 18, "leg_mode": "步行", "leg_km": 1.3,
            "poi": {"name": "西湖", "address": "", "lat": 30.0, "lng": 120.0},
        }
        alts = [{"name": "雷峰塔", "dist_km": 3.2, "leg_min": 42, "leg_mode": "打车"}]
        with mock.patch.dict(os.environ, {}, clear=False):
            text = judge_verdict(stop, alts, 90, api_key=None)
        # 模板兜底：出现真实距离 / 耗时
        self.assertIn("1.3", text)
        self.assertIn("42", text)
        self.assertIn("没选《雷峰塔》", text)

    def test_template_verdict_without_alternative_only_picks(self):
        stop = {
            "start": "18:00", "end": "19:30", "dist_km": 2.2,
            "leg_min": 30, "leg_mode": "骑行", "leg_km": 2.2,
            "poi": {"name": "文化馆", "address": "", "lat": 30.0, "lng": 120.0},
        }
        text = judge_verdict(stop, [], 90, api_key=None)
        self.assertIn("选它", text)
        self.assertIn("2.2", text)
        self.assertNotIn("没选", text, "备选不足时不硬凑对比")


class TestApplyBuffer(unittest.TestCase):
    """R1：空闲窗口两端各削 30 分钟作会议衔接缓冲。"""

    def test_shrinks_both_ends_by_30(self):
        self.assertEqual(apply_buffer([("18:00", "21:00")], 30),
                         [("18:30", "20:30")])

    def test_drops_window_shrunk_below_min_gap(self):
        # 18:00-18:40 削两端后只剩 40 分钟，不足 min_gap=45，整体丢弃
        self.assertEqual(apply_buffer([("18:00", "18:40")], 30, min_gap=45), [])

    def test_keeps_window_exactly_at_min_gap(self):
        # 18:00-20:00 削两端后 18:30-19:30 恰为 60 分钟 ≥ 45，保留
        self.assertEqual(apply_buffer([("18:00", "20:00")], 30, min_gap=45),
                         [("18:30", "19:30")])

    def test_crosses_hour_boundary_only_when_enough_room(self):
        # 22:00-23:00 削两端后 22:30-22:30 为空（<45），丢弃
        self.assertEqual(apply_buffer([("22:00", "23:00")], 30, min_gap=45), [])

    def test_multiple_windows_independently_buffered(self):
        self.assertEqual(
            apply_buffer([("12:00", "14:00"), ("17:30", "21:00")], 30),
            [("12:30", "13:30"), ("18:00", "20:30")],
        )

    def test_plan_uses_buffered_window_keeps_stops_within_buffer(self):
        # 缓冲后窗口 18:30-21:00（原 18:00-21:30），首站 start 不得早于 18:30
        lat, lng = 30.0, 120.0
        hotel = {"name": "H", "lat": lat, "lng": lng}
        pois = [{"name": "A", "lat": _north(lat, lng, 0.3)[0], "lng": lng,
                 "address": ""}]
        wins = apply_buffer([("18:00", "21:30")], 30, min_gap=45)
        stops, _ = plan(pois, hotel, wins, visit_minutes=90)
        self.assertTrue(stops, "缓冲后仍有 2 小时窗口，应能排站")
        self.assertGreaterEqual(stops[0]["start"], "18:30",
                                "首站不能压到工作衔接缓冲带内")


class TestOpenHours(unittest.TestCase):
    """景点开放时间：优先飞猪详情真实值，缺失则按品类典型窗口估算；打烊时段跳过换备选。"""

    def test_api_value_wins_over_category_rule(self):
        poi = _poi("某馆", category="博物馆", open_hours="09:00-21:00")
        self.assertEqual(resolve_open_window(poi), (540, 1260, "api"))
        self.assertNotIn("参考", open_hours_label(poi), "详情真实值不标“参考”")

    def test_all_day_text_is_parsed(self):
        self.assertEqual(resolve_open_window(_poi("广场", open_hours="全天开放")),
                         (0, 1440, "api"))

    def test_category_rule_is_marked_as_reference(self):
        poi = _poi("某馆", category="博物馆")
        self.assertEqual(resolve_open_window(poi), (540, 1020, "estimated"))
        self.assertIn("参考", open_hours_label(poi), "估算值必须标注“参考”")

    def test_keyword_source_rule_applies(self):
        self.assertEqual(resolve_open_window(_poi("小吃街", src="kw:美食街")),
                         (660, 1380, "estimated"))

    def test_unknown_has_no_constraint(self):
        poi = _poi("无信息点")
        self.assertEqual(resolve_open_window(poi), (None, None, "unknown"))
        self.assertEqual(open_hours_label(poi), "")
        self.assertTrue(_fits_open(poi, 0, 24 * 60), "无依据时不设限")

    def test_plan_skips_closed_and_picks_open_one(self):
        lat, lng = 30.0, 120.0
        museum = _poi("博物馆近", category="博物馆",
                      lat=_north(lat, lng, 0.4)[0], lng=lng)
        market = _poi("市集远", category="市集",
                      lat=_north(lat, lng, 1.0)[0], lng=lng)
        stops, notes = plan([museum, market], {"name": "H", "lat": lat, "lng": lng},
                            [("16:30", "19:00")], visit_minutes=90)
        self.assertEqual([s["poi"]["name"] for s in stops], ["市集远"],
                         "17:00 打烊的博物馆在傍晚窗口应被跳过，改选 22:00 打烊的市集")
        self.assertTrue(any(n["constraint"] == "景点开放时间" for n in notes),
                        "跳过打烊地点必须留下结构化说明")

    def test_plan_all_closed_leaves_window_empty_with_note(self):
        lat, lng = 30.0, 120.0
        museum = _poi("博物馆近", category="博物馆",
                      lat=_north(lat, lng, 0.4)[0], lng=lng)
        stops, notes = plan([museum], {"name": "H", "lat": lat, "lng": lng},
                            [("16:30", "19:00")], visit_minutes=90)
        self.assertEqual(stops, [], "全时段打烊时不得硬排")
        closing = [n for n in notes if n["constraint"] == "景点开放时间"]
        self.assertTrue(closing)
        self.assertIn("未安排景点", closing[0]["text"])

    def test_unknown_hours_keeps_old_behaviour(self):
        lat, lng = 30.0, 120.0
        poi = _poi("无信息点", lat=_north(lat, lng, 0.4)[0], lng=lng)
        stops, notes = plan([poi], {"name": "H", "lat": lat, "lng": lng},
                            [("16:30", "19:00")], visit_minutes=90)
        self.assertEqual([s["poi"]["name"] for s in stops], ["无信息点"])
        self.assertFalse(any(n["constraint"] == "景点开放时间" for n in notes))

    def test_respect_hours_false_restores_old_behaviour(self):
        lat, lng = 30.0, 120.0
        museum = _poi("博物馆近", category="博物馆",
                      lat=_north(lat, lng, 0.4)[0], lng=lng)
        stops, notes = plan([museum], {"name": "H", "lat": lat, "lng": lng},
                            [("16:30", "19:00")], visit_minutes=90,
                            respect_hours=False)
        self.assertEqual([s["poi"]["name"] for s in stops], ["博物馆近"])
        self.assertFalse(any(n["constraint"] == "景点开放时间" for n in notes))

    def _evening_stop(self, lat=30.0, lng=120.0):
        return [{"start": "16:37", "end": "18:07",
                 "poi": _poi("站A", category="市集",
                             lat=_north(lat, lng, 0.5)[0], lng=lng),
                 "dist_km": 0.5, "leg_min": 7, "leg_mode": "步行", "leg_km": 0.5}]

    def test_swap_skips_candidate_closed_at_that_time(self):
        lat, lng = 30.0, 120.0
        stops = self._evening_stop(lat, lng)
        pool = [stops[0]["poi"],
                _poi("博物馆X", category="博物馆",
                     lat=_north(lat, lng, 0.8)[0], lng=lng)]
        new_stops, note = swap_stop(stops, 0, pool, {"name": "H", "lat": lat, "lng": lng})
        self.assertTrue(note and "未开放" in note, "备选此时段打烊时不得替换")
        self.assertEqual([s["poi"]["name"] for s in new_stops], ["站A"])

    def test_swap_picks_open_candidate(self):
        lat, lng = 30.0, 120.0
        stops = self._evening_stop(lat, lng)
        pool = [stops[0]["poi"],
                _poi("博物馆X", category="博物馆",
                     lat=_north(lat, lng, 0.8)[0], lng=lng),
                _poi("夜市Y", category="市集",
                     lat=_north(lat, lng, 1.2)[0], lng=lng)]
        new_stops, note = swap_stop(stops, 0, pool, {"name": "H", "lat": lat, "lng": lng})
        self.assertIsNone(note)
        self.assertEqual(new_stops[0]["poi"]["name"], "夜市Y")


class TestNightPoi(unittest.TestCase):
    """夜景类 POI（喷泉/夜市/夜游…）不得被排到早上：全天/无依据时收窄到傍晚窗口。"""

    _DESC = ("西湖音乐喷泉位于湖滨三公园附近的湖面上，是夜游西湖不可错过的景观。"
             "晚上在这边逛街吃饭，顺便欣赏音乐喷泉，看西湖夜景，非常惬意。")

    def test_landmark_night_poi_all_day_narrowed_to_evening(self):
        # category=地标建筑 本会判成"全天"，靠简介里的夜游/晚上/夜景收窄到傍晚
        poi = _poi("西湖音乐喷泉", category="地标建筑", desc=self._DESC)
        self.assertEqual(resolve_open_window(poi), (18 * 60, 23 * 60, "estimated"))
        self.assertIn("18:00–23:00", open_hours_label(poi))
        self.assertIn("参考", open_hours_label(poi))
        self.assertTrue(_fits_open(poi, 18 * 60, 19 * 60), "傍晚时段应可排入")
        self.assertFalse(_fits_open(poi, 9 * 60, 10 * 60), "上午时段不得排入")

    def test_night_keyword_in_name_also_works(self):
        poi = _poi("河坊街夜市", category="地标建筑")
        self.assertEqual(resolve_open_window(poi), (18 * 60, 23 * 60, "estimated"))

    def test_night_poi_not_placed_in_morning_window(self):
        lat, lng = 30.0, 120.0
        poi = _poi("西湖音乐喷泉", category="地标建筑", desc=self._DESC,
                   lat=_north(lat, lng, 0.4)[0], lng=lng)
        stops, notes = plan([poi], {"name": "H", "lat": lat, "lng": lng},
                            [("09:00", "13:00")], visit_minutes=90)
        self.assertEqual(stops, [], "夜景景点不得排在上午窗口")
        self.assertTrue(any(n["constraint"] == "景点开放时间" for n in notes))

    def test_night_poi_placed_in_evening_window(self):
        lat, lng = 30.0, 120.0
        poi = _poi("西湖音乐喷泉", category="地标建筑", desc=self._DESC,
                   lat=_north(lat, lng, 0.4)[0], lng=lng)
        stops, notes = plan([poi], {"name": "H", "lat": lat, "lng": lng},
                            [("18:00", "22:00")], visit_minutes=90)
        self.assertEqual([s["poi"]["name"] for s in stops], ["西湖音乐喷泉"])
        self.assertFalse(any(n["constraint"] == "景点开放时间" for n in notes))

    def test_concrete_window_not_overridden_by_night_hint(self):
        # 有具体时段（品类规则）时不覆盖：博物馆 09:00–17:00 不受夜间兜底影响
        poi = _poi("某夜间博物馆", category="博物馆", desc="晚上有夜场灯光秀")
        self.assertEqual(resolve_open_window(poi), (540, 1020, "estimated"))

    def test_plain_all_day_poi_unaffected(self):
        # 无夜间关键词的全天 POI 保持原行为（避免误伤普通广场）
        self.assertEqual(resolve_open_window(_poi("市民广场", category="地标建筑")),
                         (0, 1440, "estimated"))


if __name__ == "__main__":
    unittest.main()
