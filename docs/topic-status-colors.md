# Current topic state and historical event colors

Starter titles remain `PR #n · title`, `ISSUE #n · title`, or `DISCUSSION #n · title`. A bold Current Status field below the branch description contains the state as inline code. Existing bot-owned starter cards are updated on their next processed event. No repository polling or bulk historical scan is added.

Current card/description colors: Open green; Draft gray; PR Merged purple; Issue Closed purple; Discussion Answered blue and Closed purple. PR Closed without merge remains red. For Discussion, accepted-answer state takes precedence over closed state. Unmarking an answer while still closed yields Closed; otherwise it yields Open. Physical thread archival still follows GitHub's closed state independently of the displayed Answered state.

All mapped original-description pages keep their contents, author, timestamp, media and message IDs while their accent and existing Current Status field follow the latest fetched state. Comments and historical event pages are not recolored by this operation. The accepted creation snapshot updates status flags without replacing cached body text. Legacy descriptions without mutable-source mappings are not guessed or edited indiscriminately.

Historical colors: ordinary comments/review comments gray; PR approval green; changes requested orange; Discussion Answer accepted blue, Answer unmarked red and Closed purple; Issue Closed red and Reopened green. Generic non-merge PR activity uses blue. Discussion pin/unpin events are consumed without thread or alert messages.

Connection authorization is unchanged: Threadle lists enabled repositories where the signed-in GitHub user has repository admin permission. Organization app-install policies may additionally require organization-owner approval.
