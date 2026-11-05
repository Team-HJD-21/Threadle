import unittest

import test_operator_web as fixtures


class LinkedAccountAdminTests(fixtures.OperatorTests):
    # Only this module's account-specific tests; base fixtures provide HTTP/JWT helpers.
    def seed(self):
        self.service.registry.add('two',22,'two/repo',202,'app',fixtures.config(2000))
        with self.service.registry.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS personal_accounts(discord TEXT PRIMARY KEY,github TEXT,login TEXT,credential BLOB,generation TEXT,updated REAL);
                CREATE TABLE IF NOT EXISTS personal_guilds(discord TEXT,guild TEXT,mentions INTEGER,comments INTEGER,consent REAL,PRIMARY KEY(discord,guild));''')
            db.executemany('INSERT INTO personal_accounts VALUES(?,?,?,?,?,?)',[
                ('55','91','alice',b'PRIVATE-TOKEN-NEVER-RENDER','PRIVATE-GENERATION',1000),
                ('66','92','bob',None,'OTHER-PRIVATE-GENERATION',2000)])
            db.executemany('INSERT INTO personal_guilds VALUES(?,?,?,?,?)',[
                ('55','1000',1,1,1000),('55','2000',0,0,2000),('66','2000',1,0,2001)])
            db.execute("INSERT OR REPLACE INTO guild_profiles VALUES('1000','Team & <one>')")

    def test_dashboard_shows_each_server_once_even_with_multiple_repositories(self):
        self.seed();self.service.registry.add('three',33,'three/repo',303,'app',fixtures.config(1000))
        self.enable();status,headers,body=self.request()
        self.assertEqual(status,200)
        self.assertEqual(body.count('href="/connect/admin/accounts/1000"'),1)
        self.assertEqual(body.count('href="/connect/admin/accounts/2000"'),1)
        self.assertIn('Team &amp; &lt;one&gt;',body)
        self.assertIn('1명',body);self.assertIn('2명',body)
        self.assertEqual(headers['Cache-Control'],'no-store')

    def test_server_scoped_identity_preferences_and_profile_links_without_secrets(self):
        self.seed();self.enable()
        status,headers,body=self.request(path='/connect/admin/accounts/1000')
        self.assertEqual(status,200)
        self.assertIn('@alice',body);self.assertNotIn('@bob',body)
        self.assertIn('https://discord.com/users/55',body)
        self.assertIn('https://github.com/alice',body)
        self.assertIn('자동 멘션</dt><dd>사용',body)
        self.assertIn('댓글 전송</dt><dd>사용',body)
        for secret in ('PRIVATE-TOKEN','PRIVATE-GENERATION','OTHER-PRIVATE','credential','refresh_token'):
            self.assertNotIn(secret,body)
        self.assertIn('noindex',headers['X-Robots-Tag'])
        status,_,other=self.request(path='/connect/admin/accounts/2000?lang=en')
        self.assertEqual(status,200);self.assertIn('@bob',other)
        self.assertIn('Automatic mentions</dt><dd>Off',other)
        self.assertIn('Comment forwarding</dt><dd>Off',other)
        self.assertNotIn('Reconnect required',other)  # Mentions-only links are valid without stored write credentials.
        self.assertIn('/connect/admin/accounts/2000?lang=ko',other)

    def test_unverified_admin_cannot_read_any_account_route(self):
        self.seed()
        self.assertEqual(self.request(path='/connect/admin/accounts/1000')[0],403)
        self.enable()
        self.assertEqual(self.request(path='/connect/admin/accounts/1000',signed=False)[0],403)
        self.assertEqual(self.request(path='/connect/admin/accounts/1000',claims=dict(self.claims,email='other@example.com'))[0],403)

    def test_disconnected_person_is_removed_and_untrusted_names_are_escaped(self):
        self.seed();self.enable()
        with self.service.registry.db() as db:
            db.execute('UPDATE personal_accounts SET login=? WHERE discord=?', ('<script>alert(1)</script>', '55'))
        body=self.request(path='/connect/admin/accounts/1000')[2]
        self.assertIn('&lt;script&gt;',body)
        self.assertNotIn('<script>alert(1)</script>',body)
        with self.service.registry.db() as db:
            db.execute("DELETE FROM personal_guilds WHERE discord='55' AND guild='1000'")
        body=self.request(path='/connect/admin/accounts/1000')[2]
        self.assertNotIn('discord.com/users/55',body)
        self.assertIn('아직 계정을 연결한 사용자가 없습니다.',body)

    def test_missing_schema_and_unknown_server_do_not_create_accounts(self):
        self.enable()
        with self.service.registry.db() as db:
            db.execute('DROP TABLE IF EXISTS personal_guilds');db.execute('DROP TABLE IF EXISTS personal_accounts')
        self.assertEqual(self.request(path='/connect/admin/accounts/1000')[0],200)
        with self.service.registry.db() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='personal_accounts'").fetchone())
        self.assertEqual(self.request(path='/connect/admin/accounts/9999')[0],404)

    def test_pagination_is_stable_and_invalid_requests_are_rejected(self):
        self.seed()
        with self.service.registry.db() as db:
            for n in range(100,151):
                db.execute('INSERT INTO personal_accounts VALUES(?,?,?,?,?,?)',(str(n),str(n+1000),'user'+str(n),None,'secret',n))
                db.execute('INSERT INTO personal_guilds VALUES(?,?,?,?,?)',(str(n),'1000',1,0,n))
        self.enable()
        first=self.request(path='/connect/admin/accounts/1000')[2]
        second=self.request(path='/connect/admin/accounts/1000?page=2')[2]
        self.assertIn('?page=2',first)
        self.assertEqual(first.count('class="admin-account-member"'),50)
        self.assertEqual(second.count('class="admin-account-member"'),2)
        self.assertIn('@user100',second);self.assertNotIn('@user100',first)
        self.assertEqual(self.request(path='/connect/admin/accounts/1000?page=0')[0],400)
        self.assertEqual(self.request(path='/connect/admin/accounts/1000?page=99999')[0],404)
        self.assertEqual(self.request(path='/connect/admin/accounts/1000?page=1&page=2')[0],400)


# Avoid running the inherited operator suite a second time; its encrypted variant
# already covers it separately.
for name in dir(fixtures.OperatorTests):
    if name.startswith('test_') and name not in LinkedAccountAdminTests.__dict__:
        setattr(LinkedAccountAdminTests,name,None)
