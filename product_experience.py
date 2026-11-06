"""Public product storytelling and accessible, selectable collaboration examples."""


def intro(t, eyebrow, title_ko, title_en, copy_ko, copy_en):
    return ('<header class="page-lead"><p class="eyebrow">'+eyebrow+'</p><h1>'+t(title_ko,title_en)+
            '</h1><p class="page-intro">'+t(copy_ko,copy_en)+'</p></header>')


def highlights(t):
    items=[('01','작업마다 이어지는 맥락','Context for every task','PR·Issue·Discussion별로 댓글과 리뷰를 모아요.','Comments and reviews stay with their PR, issue or discussion.'),
           ('02','내 계정으로 그 자리에서 답장','Reply right there, as yourself','Discord의 답장과 멘션을 GitHub 대화로 이어가요.','Discord replies and mentions continue the conversation on GitHub.'),
           ('03','바뀐 상태를 다시 찾을 필요 없이','Keep the current state in sight','상태·설명·세부 정보는 기존 카드에서 갱신돼요.','Status, descriptions and details update in the existing cards.')]
    out='<section class="product-highlights" aria-label="'+t('Threadle 핵심 기능','Threadle at a glance')+'">'
    for number,ko,en,copy_ko,copy_en in items:
        out+='<article><span class="highlight-number">'+number+'</span><h2>'+t(ko,en)+'</h2><p>'+t(copy_ko,copy_en)+'</p></article>'
    return out+'</section>'


def changes(t):
    return '<aside class="product-new"><span>'+t('최근 추가','RECENTLY ADDED')+'</span><p>'+t('내 계정으로 답장 · 양방향 멘션 · 코드 리뷰 대화 · 댓글 삭제 연동','Personal replies · Two-way mentions · Code review conversations · Linked comment deletion')+'</p><a href="#collaboration">'+t('예시로 살펴보기','See the examples')+' ↗</a></aside>'


def card(title,body,color='',author='Threadle',platform='Discord'):
    return '<article class="example-card '+color+'"><div class="example-card-top"><b>'+author+'</b><span>'+platform+'</span></div><h3>'+title+'</h3>'+body+'</article>'


def collaboration(t):
    out='<section class="collaboration-section collaboration-lab" id="collaboration" aria-labelledby="collaboration-title"><div class="section-heading"><p class="eyebrow">CONTINUE THE CONVERSATION</p><h2 id="collaboration-title">'+t('확인한 자리에서, 대화를 이어가세요.','Keep the conversation where you are.')+'</h2><p>'+t('계정은 각자 연결하고, 답장은 내 이름으로. 어떤 기능인지 예시를 골라 확인해 보세요.','Link your own account and reply as yourself. Choose an example to see how it works.')+'</p></div>'
    out+='<fieldset class="collaboration-picker"><legend class="sr-only">'+t('협업 기능 예시 선택','Choose a collaboration example')+'</legend>'
    for i,(key,ko,en) in enumerate([('reply','내 이름으로 답장','Reply as yourself'),('review','코드 리뷰 대화','Code review conversations'),('update','상태와 내용 동기화','Keep updates in sync')]):
        out+='<input class="sr-only" type="radio" name="collaboration-example" id="collaboration-'+key+'" value="'+key+'"'+(' checked' if i==0 else '')+'><label for="collaboration-'+key+'">'+t(ko,en)+'</label>'
    out+='<div class="collaboration-panels">'
    out+='<div class="collaboration-panel lab-reply"><div class="example-context"><span>PR #42</span><p>'+t('같은 대화, 서로의 계정으로.','The same conversation. Your own identities.')+'</p></div><div class="example-pair">'
    reply='<p><span class="collaboration-mention">@Jae</span> '+t('예외 처리도 확인해 주세요.','Could you check the error handling too?')+'</p>'
    out+=card('PR #42 · '+t('설정 화면 개선','Improve settings'),reply,'example-neutral','Min')
    out+='<span class="example-transfer" aria-hidden="true">→</span>'
    reply='<p><span class="collaboration-mention">@jae-dev</span> '+t('예외 처리도 확인해 주세요.','Could you check the error handling too?')+'</p>'
    out+=card(t('Min의 GitHub 계정으로 등록','Posted from Min’s GitHub account'),reply,'example-neutral','min-dev','GitHub')
    out+='</div><p class="example-note">'+t('연결된 팀원의 실제 Discord 멘션이 GitHub 아이디로 바뀝니다. 일반 알림에 답장하면 원문을 인용해요.','Native Discord mentions of linked teammates become GitHub usernames. Reply to a notification to quote its original.')+'</p></div>'
    out+='<div class="collaboration-panel lab-review"><div class="example-context"><span>PR #42 · Review</span><p>'+t('코드 한 곳의 대화를, 카드 하나에.','One conversation about the code. One card.')+'</p></div><div class="review-example-grid">'
    body='<p class="example-state">Current Status <code>Resolved</code></p><span class="example-file">Settings.cs:42</span><pre><code><span class="example-line">41</span> if (!settings.IsValid)\n<span class="example-line">42</span>     return Result.Invalid;</code></pre><div class="example-replies"><p><b>Min</b><span>11/06 · 14:32</span><br>'+t('설정이 비어 있는 경우도 처리하면 좋겠어요.','Could we handle empty settings too?')+'</p><p><b>Jae</b><span>11/06 · 14:36</span><br>'+t('확인했어요. 검증을 추가했습니다.','Done — I added validation.')+'</p></div>'
    out+=card('PR #42 · Review thread',body,'example-resolved')
    out+='<div class="example-explanation"><span class="example-status-pill">'+t('답글은 같은 카드에','Replies stay together')+'</span><h3>'+t('답장부터 Resolve까지.','From reply to resolved.')+'</h3><p>'+t('Discord의 답장 기능으로 코드 리뷰 카드에 답하면 해당 GitHub 리뷰 대화에 등록됩니다. 새 답글은 기존 카드에 모이고, 해결되면 빨강에서 초록으로 바뀌어요.','Reply to a code review card in Discord to post in that GitHub review conversation. Replies update the same card, and its edge changes from red to green when resolved.')+'</p><p class="muted">'+t('카드 반영 후에는 입력 메시지도 자동 정리합니다. 봇에 메시지 관리 권한이 필요합니다.','The input is cleaned up after the card updates. The bot needs Manage Messages permission.')+'</p></div></div></div>'
    out+='<div class="collaboration-panel lab-update"><div class="example-context"><span>PR #42</span><p>'+t('새 알림을 쌓기보다, 최신 상태로.','Keep the latest state, without stacking notifications.')+'</p></div><div class="example-pair">'
    title='PR #42 · '+t('설정 화면 개선','Improve settings')
    out+=card(title,'<p><code>main</code> ← <code>feature/settings</code></p><p class="example-state">Current Status <code>Open</code></p>','example-open')
    out+='<span class="example-transfer" aria-hidden="true">→</span>'
    out+=card(title,'<p><code>main</code> ← <code>feature/settings</code></p><p class="example-state">Current Status <code>Merged</code></p>','example-merged')
    out+='</div><p class="example-note">'+t('상태·라벨·담당자는 기존 카드에 반영합니다. 설명이 바뀌면 수정 사실을 알리고, 긴 설명은 한 묶음으로 유지해요. 전송한 Discord 원본을 지우면 연결된 GitHub 댓글도 삭제합니다.','Status, labels and assignees update the existing card. Description changes are announced, and long descriptions stay together. Deleting a forwarded Discord original also removes its linked GitHub comment.')+'</p></div></div></fieldset>'
    out+='<div class="collaboration-footnote"><p>'+t('작동을 설명하는 예시입니다. 개인 댓글 전송은 선택 사항이며, 각자 /account connect로 연결합니다.','Illustrative examples. Personal comment forwarding is optional; each teammate links their own account with /account connect.')+'</p><a class="button secondary" href="/connect/setup#account-setup">'+t('계정 연결 방법','How to link your account')+' →</a></div></section>'
    return out


def closing(t):
    return '<section class="product-closing"><div><p class="eyebrow">START WITH YOUR TEAM</p><h2>'+t('다음 PR부터, 한곳에서 이어가세요.','Keep your next PR in one place.')+'</h2><p>'+t('서버 관리자는 저장소를 연결하고, 팀원은 원할 때 자기 계정을 연결하면 됩니다.','An admin connects the repository. Teammates can then choose to link their own accounts.')+'</p></div><div><a class="button" href="/connect/setup">'+t('연결 방법 보기','See setup steps')+' →</a><a href="https://discord.gg/6faZbNBdv" target="_blank" rel="noopener noreferrer">'+t('공개 테스트 · Support 커뮤니티','Try it in the Support community')+' ↗</a></div></section>'
