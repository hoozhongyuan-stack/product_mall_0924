from django.test import SimpleTestCase
from shipping.rules import courier_cost, point_in_polygon, delivery_zone, apply_funding, ShippingRuleError


class RuleTests(SimpleTestCase):
    def test_integer_fen_weight_rounding(self):
        rule = dict(mode='WEIGHT', firstFeeFen=800, firstUnits=1000, extraFeeFen=200, extraUnits=1000)
        self.assertEqual(courier_cost(rule, 3, 2300), 1200)
        self.assertEqual(courier_cost(rule, 3, 1000), 800)
        with self.assertRaises(ShippingRuleError): courier_cost(rule, 3, None)

    def test_piece_fixed_and_rounding(self):
        self.assertEqual(courier_cost(dict(mode='PIECE', firstFeeFen=29, firstUnits=2, extraFeeFen=3, extraUnits=2), 5, None), 35)
        self.assertEqual(courier_cost(dict(mode='FIXED', firstFeeFen=29), 99, None), 29)

    def test_polygon_boundary_counts_inside(self):
        points = [dict(latitude=0, longitude=0), dict(latitude=0, longitude=1), dict(latitude=1, longitude=1), dict(latitude=1, longitude=0)]
        self.assertTrue(point_in_polygon(dict(latitude=0, longitude=.5), points))
        self.assertTrue(point_in_polygon(dict(latitude=.5, longitude=.5), points))
        self.assertFalse(point_in_polygon(dict(latitude=2, longitude=.5), points))

    def test_exclusion_overrides_priority_and_overnight_times(self):
        circle = dict(id='a', enabled=True, kind='CIRCLE', center=dict(latitude=0, longitude=0), radiusMeters=1000, priority=10, startTime='23:00', endTime='06:00')
        point = dict(latitude=0, longitude=0)
        exclusion = dict(circle, id='b', kind='EXCLUDE', polygon=[dict(latitude=-1,longitude=-1),dict(latitude=1,longitude=-1),dict(latitude=1,longitude=1)], priority=99)
        with self.assertRaises(ShippingRuleError): delivery_zone([circle, exclusion], point, '01:00')
        self.assertEqual(delivery_zone([circle], point, '01:00')['id'], 'a')
        with self.assertRaises(ShippingRuleError): delivery_zone([circle], point, '12:00')

    def test_platform_priority_and_exact_threshold(self):
        self.assertEqual(apply_funding(1200, 19900, 19900, False), dict(costFen=1200, customerFeeFen=0, warehouseBearsFen=1200, platformBearsFen=0, freeSource='WAREHOUSE'))
        self.assertEqual(apply_funding(1200, 19900, 19900, True)['platformBearsFen'], 1200)
        self.assertEqual(apply_funding(1200, 19899, 19900, False)['customerFeeFen'], 1200)

    def test_root_region_keyword_cannot_bypass_parent_ancestry(self):
        from django.http import QueryDict
        from shipping.regions import region_children
        from unittest.mock import patch
        with patch('shipping.regions.effective_configuration') as upstream:
            with self.assertRaises(ValueError):region_children(QueryDict('keywords=长春市'))
            upstream.assert_not_called()

    def test_same_district_names_match_ancestors(self):
        from shipping.service import _region_matches
        region=dict(code='220104',name='朝阳区',level='district',ancestors=[dict(code='220000',name='吉林省',level='province'),dict(code='220100',name='长春市',level='city')])
        self.assertTrue(_region_matches(region,dict(province='吉林省',city='长春市',district='朝阳区')))
        self.assertFalse(_region_matches(region,dict(province='北京市',city='北京市',district='朝阳区')))

    def test_official_region_children_keep_ancestry_and_cached_reselection(self):
        import json
        from django.http import QueryDict
        from django.core.cache import cache
        from shipping.regions import region_children,RegionUnavailable
        from unittest.mock import patch,MagicMock
        cache.clear()
        with self.assertRaises(RegionUnavailable):region_children(QueryDict('parentAdcode=220100'))
        responses=[dict(status='1',districts=[dict(adcode='100000',districts=[dict(adcode='220000',name='吉林省',level='province')])]),dict(status='1',districts=[dict(adcode='220000',districts=[dict(adcode='220100',name='长春市',level='city')])]),dict(status='1',districts=[dict(adcode='220100',districts=[dict(adcode='220104',name='朝阳区',level='district')])])]
        opener=MagicMock()
        context=opener.open.return_value.__enter__.return_value
        context.read.side_effect=[json.dumps(row).encode() for row in responses]
        with patch('shipping.regions.effective_configuration',return_value=dict(webServiceKey='synthetic-test-key')),patch('shipping.regions.build_opener',return_value=opener):
            self.assertEqual(region_children(QueryDict())[0]['name'],'吉林省')
            self.assertEqual(region_children(QueryDict('parentAdcode=220000'))[0]['name'],'长春市')
            district=region_children(QueryDict('parentAdcode=220100'))[0]
            self.assertEqual([row['name'] for row in district['ancestors']],['吉林省','长春市'])
            self.assertEqual(region_children(QueryDict('parentAdcode=220100'))[0],district)
            self.assertEqual(opener.open.call_count,3)
        cache.clear()

    def test_region_service_missing_key_and_upstream_rejection_are_safe(self):
        from django.http import QueryDict
        from django.core.cache import cache
        from shipping.regions import region_children,RegionUnavailable
        from unittest.mock import patch,MagicMock
        cache.clear()
        with patch('shipping.regions.effective_configuration',return_value=dict(webServiceKey='')):
            with self.assertRaises(RegionUnavailable):region_children(QueryDict())
        opener=MagicMock();opener.open.side_effect=OSError('synthetic timeout')
        with patch('shipping.regions.effective_configuration',return_value=dict(webServiceKey='synthetic-test-key')),patch('shipping.regions.build_opener',return_value=opener):
            with self.assertRaises(RegionUnavailable):region_children(QueryDict())
