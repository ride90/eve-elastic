# -*- coding: utf-8 -*-

import eve
import elasticsearch
from unittest import TestCase
from datetime import datetime
from flask import json
from eve.utils import config, ParsedRequest
from ..elastic import parse_date, Elastic
from bson.objectid import ObjectId
from nose.tools import raises


DOMAIN = {
    'items': {
        'schema': {
            'uri': {'type': 'string', 'unique': True},
            'name': {'type': 'string'},
            'firstcreated': {'type': 'datetime'},
            'category': {
                'type': 'string',
                'mapping': {'type': 'string', 'index': 'not_analyzed'}
            },
        },
        'datasource': {
            'backend': 'elastic',
            'projection': {'firstcreated': 1, 'name': 1},
            'default_sort': [('firstcreated', -1)]
        }
    },
    'items_with_description': {
        'schema': {
            'uri': {'type': 'string', 'unique': True},
            'name': {'type': 'string'},
            'description': {'type': 'string'},
            'firstcreated': {'type': 'datetime'},
        },
        'datasource': {
            'backend': 'elastic',
            'projection': {'firstcreated': 1, 'name': 1},
            'default_sort': [('firstcreated', -1)],
            'elastic_filter': {'exists': {'field': 'description'}},
            'aggregations': {'type': {'terms': {'field': 'name'}}}
        }
    }
}


INDEX = 'elastic_tests'
DOC_TYPE = 'items'


class TestElastic(TestCase):

    def setUp(self):
        settings = {'DOMAIN': DOMAIN}
        settings['ELASTICSEARCH_URL'] = 'http://localhost:9200'
        settings['ELASTICSEARCH_INDEX'] = INDEX
        self.app = eve.Eve(settings=settings, data=Elastic)
        with self.app.app_context():
            self.app.data.remove('items')
            self.app.data.remove('items_with_description')

    def test_parse_date(self):
        date = parse_date('2013-11-06T07:56:01.414944+00:00')
        self.assertIsInstance(date, datetime)
        self.assertEquals('07:56+0000', date.strftime('%H:%M%z'))

    def test_put_mapping(self):
        elastic = Elastic(None)
        elastic.init_app(self.app)
        elastic.put_mapping(self.app)

        mapping = elastic.get_mapping(elastic.index)[elastic.index]
        items_mapping = mapping['mappings']['items']['properties']

        self.assertIn('firstcreated', items_mapping)
        self.assertEquals('date', items_mapping['firstcreated']['type'])

        self.assertIn(config.DATE_CREATED, items_mapping)
        self.assertIn(config.LAST_UPDATED, items_mapping)

        self.assertIn('uri', items_mapping)
        self.assertIn('category', items_mapping)

    def test_dates_are_parsed_on_fetch(self):
        with self.app.app_context():
            self.app.data.insert('items', [{'uri': 'test', 'firstcreated': '2012-10-10T11:12:13+0000'}])
            item = self.app.data.find_one('items', req=None, uri='test')
            self.assertIsInstance(item['firstcreated'], datetime)

    def test_query_filter_with_filter_dsl_and_schema_filter(self):
        with self.app.app_context():
            self.app.data.insert('items_with_description', [
                {'uri': 'u1', 'name': 'foo', 'firstcreated': '2012-01-01T11:12:13+0000'},
                {'uri': 'u2', 'name': 'foo', 'firstcreated': '2013-01-01T11:12:13+0000'},
                {'uri': 'u3', 'name': 'foo', 'description': 'test', 'firstcreated': '2013-01-01T11:12:13+0000'},
            ])

        query_filter = {
            'term': {'name': 'foo'}
        }

        with self.app.app_context():
            req = ParsedRequest()
            req.args = {'filter': json.dumps(query_filter)}
            self.assertEquals(1, self.app.data.find('items_with_description', req, None).count())

        with self.app.app_context():
            req = ParsedRequest()
            req.args = {'q': 'bar', 'filter': json.dumps(query_filter)}
            self.assertEquals(0, self.app.data.find('items_with_description', req, None).count())

    def test_find_one_by_id(self):
        """elastic 1.0+ is using 'found' property instead of 'exists'"""
        with self.app.app_context():
            self.app.data.insert('items', [{'uri': 'test', config.ID_FIELD: 'testid'}])
            item = self.app.data.find_one('items', req=None, **{config.ID_FIELD: 'testid'})
            self.assertEquals('testid', item[config.ID_FIELD])

    def test_formating_fields(self):
        """when using elastic 1.0+ it puts all requested fields values into a list
        so instead of {"name": "test"} it returns {"name": ["test"]}"""
        with self.app.app_context():
            self.app.data.insert('items', [{'uri': 'test', 'name': 'test'}])
            item = self.app.data.find_one('items', req=None, uri='test')
            self.assertEquals('test', item['name'])

    def test_search_via_source_param(self):
        query = {'query': {'term': {'uri': 'foo'}}}
        with self.app.app_context():
            self.app.data.insert('items', [{'uri': 'foo', 'name': 'foo'}])
            self.app.data.insert('items', [{'uri': 'bar', 'name': 'bar'}])
            req = ParsedRequest()
            req.args = {'source': json.dumps(query)}
            res = self.app.data.find('items', req, None)
            self.assertEquals(1, res.count())

    def test_search_via_source_param_and_schema_filter(self):
        query = {'query': {'term': {'uri': 'foo'}}}
        with self.app.app_context():
            self.app.data.insert('items_with_description', [{'uri': 'foo', 'description': 'test', 'name': 'foo'}])
            self.app.data.insert('items_with_description', [{'uri': 'bar', 'name': 'bar'}])
            req = ParsedRequest()
            req.args = {'source': json.dumps(query)}
            res = self.app.data.find('items_with_description', req, None)
            self.assertEquals(1, res.count())

    def test_mapping_is_there_after_delete(self):
        with self.app.app_context():
            self.app.data.put_mapping(self.app)
            mapping = self.app.data.get_mapping(INDEX, DOC_TYPE)
            self.app.data.remove('items')
            self.assertEquals(mapping, self.app.data.get_mapping(INDEX, DOC_TYPE))

    def test_find_one_raw(self):
        with self.app.app_context():
            ids = self.app.data.insert('items', [{'uri': 'foo', 'name': 'foo'}])
            item = self.app.data.find_one_raw('items', ids[0])
            self.assertEquals(item['name'], 'foo')

    def test_is_empty(self):
        with self.app.app_context():
            self.assertTrue(self.app.data.is_empty('items'))
            self.app.data.insert('items', [{'uri': 'foo'}])
            self.assertFalse(self.app.data.is_empty('items'))

    def test_storing_objectid(self):
        with self.app.app_context():
            res = self.app.data.insert('items', [{'uri': 'foo', 'user': ObjectId('528de7b03b80a13eefc5e610')}])
            self.assertEquals(1, len(res))

    def test_replace(self):
        with self.app.app_context():
            res = self.app.data.insert('items', [{'uri': 'foo', 'user': ObjectId('528de7b03b80a13eefc5e610')}])
            self.assertEquals(1, len(res))
            new_item = {'uri': 'bar', 'user': ObjectId('528de7b03b80a13eefc5d456')}
            res = self.app.data.replace('items', res.pop(), new_item)
            self.assertEquals(2, res['_version'])

    def test_sub_resource_lookup(self):
        with self.app.app_context():
            self.app.data.insert('items', [{'uri': 'foo', 'name': 'foo'}])
            req = ParsedRequest()
            req.args = {}
            self.assertEquals(1, self.app.data.find('items', req, {'name': 'foo'}).count())
            self.assertEquals(0, self.app.data.find('items', req, {'name': 'bar'}).count())

    def test_sub_resource_lookup_with_schema_filter(self):
        with self.app.app_context():
            self.app.data.insert('items_with_description', [{'uri': 'foo', 'description': 'test', 'name': 'foo'}])
            req = ParsedRequest()
            req.args = {}
            self.assertEquals(1, self.app.data.find('items_with_description', req, {'name': 'foo'}).count())
            self.assertEquals(0, self.app.data.find('items_with_description', req, {'name': 'bar'}).count())

    def test_resource_filter(self):
        with self.app.app_context():
            self.app.data.insert('items_with_description', [{'uri': 'foo', 'description': 'test'}, {'uri': 'bar'}])
            req = ParsedRequest()
            req.args = {}
            req.args['source'] = json.dumps({'query': {'filtered': {'filter': {'term': {'uri': 'bar'}}}}})
            self.assertEquals(0, self.app.data.find('items_with_description', req, None).count())

    def test_update(self):
        with self.app.app_context():
            ids = self.app.data.insert('items', [{'uri': 'foo'}])
            self.app.data.update('items', ids[0], {'uri': 'bar'})
            self.assertEquals(self.app.data.find_one('items', req=None, _id=ids[0])['uri'], 'bar')

    def test_remove_non_existing_item(self):
        with self.app.app_context():
            self.assertEquals(self.app.data.remove('items', {'_id': 'notfound'}), None)

    @raises(elasticsearch.exceptions.ConnectionError)
    def test_it_can_use_configured_url(self):
        with self.app.app_context():
            self.app.config['ELASTICSEARCH_URL'] = 'http://localhost:9292'
            Elastic(self.app)

    def test_resource_aggregates(self):
        with self.app.app_context():
            self.app.data.insert('items_with_description', [{'uri': 'foo1', 'description': 'test', 'name': 'foo'}])
            self.app.data.insert('items_with_description', [{'uri': 'foo2', 'description': 'test1', 'name': 'foo'}])
            self.app.data.insert('items_with_description', [{'uri': 'foo3', 'description': 'test2', 'name': 'foo'}])
            self.app.data.insert('items_with_description', [{'uri': 'bar1', 'description': 'test3', 'name': 'bar'}])
            req = ParsedRequest()
            req.args = {}
            response = {}
            item1 = self.app.data.find('items_with_description', req, {'name': 'foo'})
            item2 = self.app.data.find('items_with_description', req, {'name': 'bar'})
            item1.extra(response)
            self.assertEquals(3, item1.count())
            self.assertEquals(1, item2.count())
            self.assertEquals(3, response['_aggregations']['type']['buckets'][0]['doc_count'])
