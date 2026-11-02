import http.client
import unittest
from unittest.mock import Mock

from connect_web import page
from test_connect import WebFixture, Browser
from web_locale import LANGUAGE, text


class LanguageFlowTests(WebFixture):
    def test_language_choice_persists_and_does_not_change_another_visitors_language(self):
        result = self.browser.request('GET', '/connect?lang=en')
        self.assertEqual(result[0], 200)
        self.assertEqual(result[1]['Content-Language'], 'en')
        self.assertIn('<html lang="en">', result[2])
        self.assertIn('TeamHJD - Threadle', result[2])
        self.assertEqual(self.browser.language, 'en')
        self.assertIn('Start free', self.browser.request('GET', '/connect')[2])
        other = Browser(self.server)
        self.assertIn('무료로 시작하기', other.request('GET', '/connect')[2])
        self.assertIn('Start free', self.browser.request('GET', '/connect?lang=javascript:alert(1)')[2])
        self.assertIn('무료로 시작하기', self.browser.request('GET', '/connect?lang=ko')[2])

    def test_english_survives_both_oauth_flows_and_renders_completion_and_errors(self):
        self.browser.request('GET', '/connect?lang=en')
        self.configure()
        repositories = self.browser.request('GET', '/connect/repositories')
        self.assertIn('Choose a GitHub repository', repositories[2])
        self.assertIn('target="_blank" rel="noopener noreferrer"', repositories[2])
        self.assertIn('GitHub settings open in a new tab.', repositories[2])
        setup = self.browser.request('GET', '/connect/channels')
        self.assertIn('Create channels and connect', setup[2])
        self.assertIn('Leave blank for all workflows', setup[2])
        self.assertEqual(self.post('/connect/save')[0], 303)
        self.assertIn('You are connected!', self.browser.request('GET', '/connect/done')[2])
        self.assertIn('This page could not be found.', self.browser.request('GET', '/connect/missing')[2])
        self.assertEqual(self.browser.language, 'en')

    def test_server_names_and_registered_business_details_are_not_translated(self):
        self.browser.request('GET', '/connect?lang=en')
        self.configure()
        self.users.guilds = Mock(return_value=[{'id': '10', 'name': '로그아웃', 'permissions': '32'}])
        server = self.browser.request('GET', '/connect/server')[2]
        self.assertIn('<strong>로그아웃</strong>', server)
        self.assertIn('Sign out</button>', server)
        self.assertIn('Business registration number', server)
        self.assertIn('2026-수원영통-1221호', server)
        self.assertIn('Business address', server)

    def test_browser_language_is_used_only_until_the_user_makes_a_choice(self):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request('GET', '/connect', headers={'Host': 'notify.example', 'Accept-Language': 'en-US,en;q=0.9'})
            response = connection.getresponse()
            self.assertEqual(response.getheader('Content-Language'), 'en')
            self.assertIn('Start free', response.read().decode())
        finally:
            connection.close()

    def test_language_switcher_never_copies_oauth_codes_into_its_links(self):
        self.browser.request('GET', '/connect?lang=en')
        body = self.browser.request('GET', '/connect/discord/callback?code=private-test-code&state=invalid')[2]
        self.assertIn('href="/connect?lang=ko"', body)
        self.assertNotIn('private-test-code', body)


class EnglishCopyTests(unittest.TestCase):
    def test_provider_errors_keep_reference_ids_and_are_readable_in_english(self):
        token = LANGUAGE.set('en')
        try:
            self.assertEqual(text('Discord 요청이 잠시 제한됐습니다. 12초 뒤 현재 단계에서 다시 시도해 주세요. (확인 번호: abc123)'),
                             'Discord has temporarily limited requests. Try this step again in 12 seconds. (Reference: abc123)')
            self.assertIn('Reference: abc123', text('GitHub에서 요청을 완료하지 못했습니다. 현재 단계에서 다시 시도해 주세요. (확인 번호: abc123)'))
            self.assertIn('lang="en"', page('연결을 확인해 주세요.', '<p>페이지를 찾을 수 없습니다.</p>'))
            self.assertIn('<title>TeamHJD - Threadle | We could not complete this step.</title>', page('연결을 확인해 주세요.', ''))
        finally:
            LANGUAGE.reset(token)
        self.assertIn('lang="ko"', page('개발 소식을 한곳에.', ''))
