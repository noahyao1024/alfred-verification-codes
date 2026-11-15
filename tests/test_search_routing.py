"""Search refinements must stay local and keep the selected account context."""

import json
import tempfile
import unittest
from unittest.mock import patch

from test_apple_passwords import app
from passwords_bridge.aliases import AliasStore
from passwords_bridge.menu import catalog_menu


BINDING = {'domain': 'github.com', 'identity': {'kind': 'password',
           'username': 'alice', 'title': 'Work', 'sites': ['github.com']}}
EMPTY = {'ok': True, 'mode': 'password', 'accounts': [], 'fallback': False}


class SearchRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = AliasStore(self.temp.name)
        self.store.put('gh-work', BINDING)
    def tearDown(self): self.temp.cleanup()

    def test_alias_prefix_is_local_with_an_explicit_website_option(self):
        with patch.object(app, 'request_catalog', return_value=EMPTY) as request:
            response = app.direct_menu('gh', self.store)
        request.assert_not_called()
        actions = [json.loads(row['arg']) for row in response['items'] if 'arg' in row]
        self.assertEqual(actions[0], {'action': 'login', 'alias': 'gh-work'})
        self.assertIn({'action': 'search', 'query': 'gh.com'}, actions)

    def test_explicit_website_still_queries_even_with_matching_alias_prefix(self):
        self.store.put('github-work', BINDING)
        with patch.object(app, 'request_catalog', return_value=EMPTY) as request:
            app.direct_menu('github.com', self.store)
        self.assertEqual(request.call_args.args[:2], ('github.com', 'password'))

    def test_explicit_alias_search_never_looks_up_a_website(self):
        with patch.object(app, 'request_catalog', return_value=EMPTY) as request:
            found = app.direct_menu('@gh', self.store)
            missing = app.direct_menu('@missing', self.store)
        request.assert_not_called()
        self.assertEqual(json.loads(found['items'][0]['arg'])['alias'], 'gh-work')
        self.assertIn('别名', missing['items'][0]['title'])
        self.assertFalse(missing['items'][0]['valid'])

    def test_exact_and_chinese_aliases_remain_available(self):
        self.store.put('工作', BINDING)
        with patch.object(app, 'request_catalog', return_value=EMPTY) as request:
            for query, expected in [('gh-work', 'gh-work'), ('工作', '工作'), ('@工作', '工作')]:
                response = app.direct_menu(query, self.store)
                self.assertEqual(json.loads(response['items'][0]['arg'])['alias'], expected)
        request.assert_not_called()

    def test_switching_between_passwords_and_codes_preserves_filter(self):
        response = catalog_menu('github.com', [], 'alice 工作', mode='password')
        action = json.loads(response['items'][-1]['arg'])
        self.assertEqual(action, {'action': 'search', 'query': 'codes github.com alice 工作'})
        response = catalog_menu('github.com', [], 'alice 工作', mode='otp')
        self.assertEqual(json.loads(response['items'][-1]['arg']),
                         {'action': 'search', 'query': 'github.com alice 工作'})


if __name__ == '__main__': unittest.main()
