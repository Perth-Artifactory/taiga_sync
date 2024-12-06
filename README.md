# Taiga sync

CRM-ish workflows tacked onto a kanban board innit

## Kanban/Taiga nomenclature

* Trello is another example of a **Kanban board** (we don't use it anymore because it's no longer free)
* Each card on a Kanban board is a **user story**
* Cards sit in one column at a time which is their **status**
* Things that need to be done for a user story are attached as **tasks**
* Some boards have a separate tab for **Issues** that we use primarily to track things reported from outside Taiga
* Some boards have a separate tab for **Epics**. These are used to track a collection of user stories into a large cohesive project.

## Slack app

`slack_app.py`

Exposes many of Taiga's functions via a Slack interface.

## Attendee tracking

Tracks attendee interactions with the AF bureaucracy

### Usage

`attendee.py`

* `--import` will create cards based on TidyHQ data.
* `--force` will override the presence of a lock file.

### Nomenclature

* Each attendee is assigned a **user story**
* **Statuses** are a representation of the pathway taken by attendees

### Statuses

| ID | Status                | Description                                        |
|----|-----------------------|----------------------------------------------------|
| 1  | Intake                | Where new email queries appear                     |
| 2  | Prospective           | Someone that might attend the space                |
| 3  | Attendee              | Day Pass attendees                                 |
| 4  | New Member            | People that have signed up as a member             |
| 5  | Member                | People that have started to "settle in" as members |
| 6  | Prospective Keyholder | People that should be considered for a key         |
| 7  | Keyholder             | People going through the keyholder process         |

### Tasks

Tasks are arranged so that once all tasks in a status are complete the attendee is considered a "good \<category\>"

| Task                              | Templated in     | Default Status  |  Closed by code  | Closed by status | Removed by status | Position justification                                                                                                                                      |
|-----------------------------------|------------------|-----------------|------------------|-------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Respond to query                  | 1.Intake         | Not started     | ❔Req email proc | ✅3.Attendee      | 4.New Member      | The root status that user stories are created under when they start as an email query                                                                       |
| Determine project viability       | 2.Prospective    | Not started     | ❔Req email gpt  | ➖ N/A            | 5.Member (comp)   | Could conceivably be moved to 3.Attendee but this way we can mark the task as failed if it's clear from an enquiry email that we're not a suitable workshop |
| Encourage to visit                | 2.Prospective    | Optional        | ❔Req email gpt  | ✅5.Member        | N/A               | The primary target/goal for this status                                                                                                                     |
| Visit                             | 2.Prospective    | Not started     | ➖ N/A           | ✅3.Attendee      | 4.New Member      | Primarily used to trigger the move to the next status                                                                                                       |
| Join Slack                        | 3.Attendee       | External        | ✅               | ➖ N/A            | N/A               | Slack sign up is typically promoted during the first visit. Until this happens it shouldn't be treated as a blocker for progression                         |
| Participated in an event          | 3.Attendee       | Optional        | ➖ N/A           | ➖ N/A            | 7.Keyholder       | The primary target/goal for this status                                                                                                                     |
| Signed up as a visitor            | 3.Attendee       | External        | ✅               | ➖ N/A            | 4.New Member      | Not required in the earlier statuses                                                                                                                        |
| Valid emergency contact details   | 3.Attendee       | External        | ✅               | ➖ N/A            | 4.New Member      | Required for all attendees                                                                                                                                  |
| Discussed moving to membership    | 3.Attendee       | Optional        | ✅               | ➖ N/A            | 4.New Member      | Some attendees don't want to progress to membership, included here so we can indicate/track that                                                            |
| Completed new visitor induction   | 3.Attendee       | External        | ✅               | ➖ N/A            | 4.New Member      | A required step for attendees                                                                                                                               |
| Signed up as member               | 3.Attendee       | External        | ✅               | ✅4.New Member    | 5.Member          | Here primarily to allow for programmatic progression                                                                                                        |
| New member induction              | 4.New Member     | External        | ✅               | ➖ N/A            | N/A               | A required step for new members                                                                                                                             |
| Planned first project             | 4.New Member     | Not started     | ✅ 6mo member    | ➖ N/A            | N/A               | A key to success as a member. Tracked here instead of in 3.Attendee because we don't offer the same level of support with this to non members               |
| Attending events as a member      | 4.New Member     | Optional        | ➖ N/A           | ➖ N/A            | 6.Pros Keyholder  | Getting new members settled into events is key                                                                                                              |
| Added to billing groups           | 4.New Member     | External        | ✅               | ➖ N/A            | N/A               | A required bureaucratic step for new members                                                                                                                |
| Received at least one induction   | 4.New Member     | External        | ✅               | ➖ N/A            | N/A               |                                                                                                                                                             |
| Concession proof sighted          | 4.New Member     | External        | ✅               | ➖ N/A            | N/A               | Typically completed as part of new member induction                                                                                                         |
| Held membership for 2+ weeks      | 4.New Member     | External        | ✅ 2wk member    | ✅7.Keyholder     | N/A               |                                                                                                                                                             |
| Communicated an issue effectively | 4.New Member     | Optional        | ➖ N/A           | ➖ N/A            | ➖ N/A             |                                                                                                                                                             |
| No indication of CoC violations   | 5.Member         | Not started     | ✅ has_key       | ✅7.Keyholder     | 8.Settled         |                                                                                                                                                             |
| Competent to let people in        | 5.Member         | Not started     | ✅ has_key       | ✅7.Keyholder     | 8.Settled         |                                                                                                                                                             |
| Can work unsupervised             | 5.Member         | Not started     | ✅ has_key       | ✅7.Keyholder     | 8.Settled         |                                                                                                                                                             |
| Undertakes tasks safely           | 5.Member         | Not started     | ✅ has_key       | ✅7.Keyholder     | 8.Settled         |                                                                                                                                                             |
| Cleans work area                  | 5.Member         | Not started     | ✅ has_key       | ✅7.Keyholder     | 8.Settled         |                                                                                                                                                             |
| Communicates issues to mngt       | 5.Member         | Not started     | ✅ has_key       | ✅7.Keyholder     | 8.Settled         |                                                                                                                                                             |
| Pays invoices without issue       | 5.Member         | Not started     | ✅ 18mo member   | ✅7.Keyholder     | 8.Settled         |                                                                                                                                                             |
| Suggested key application/nom     | 5.Member         | Blocked         | ✅ has_key       | ✅7.Keyholder     | 8.Settled         | Tracking when a responsible person is willing to push for a key                                                                                             |
| Keyholder motion put to vote      | 6.Pros Keyholder | Not started     | ✅ has_key       | ✅7.Keyholder     | N/A               | A required bureaucratic step for keyholder applications                                                                                                     |
| Keyholder motion successful       | 6.Pros Keyholder | Blocked         | ✅ has_key       | ✅7.Keyholder     | N/A               | A required bureaucratic step for keyholder applications                                                                                                     |
| Confirmed photo on tidyhq         | 6.Pros Keyholder | External        | ✅               | ➖ N/A            | N/A               | A required bureaucratic step for keyholder applications                                                                                                     |
| Confirmed paying via bank         | 6.Pros Keyholder | External        | ✅               | ➖ N/A            | N/A               | A required bureaucratic step for keyholder applications                                                                                                     |
| Send keyholder documentation      | 6.Pros Keyholder | Blocked         | ✅ has_key       | ✅7.Keyholder     | N/A               | A required bureaucratic step for keyholder applications                                                                                                     |
| Send bond invoice                 | 6.Pros Keyholder | External        | ✅               | ✅7.Keyholder     | N/A               | A required bureaucratic step for keyholder applications                                                                                                     |
| Confirm bond invoice paid         | 6.Pros Keyholder | External        | ✅               | ✅7.Keyholder     | N/A               | A required bureaucratic step for keyholder applications                                                                                                     |
| Keyholder induction completed     | 6.Pros Keyholder | External        | ✅               | ➖ N/A            | N/A               | A required bureaucratic step for keyholder applications                                                                                                     |
| 3 month check in post key         | 7.Keyholder      | Not started     | ➖ N/A           | ➖ N/A            | N/A               | Some members will rest here                                                                                                                                 |
| Discussed volunteering            | 7.Keyholder      | Not started     | ➖ N/A           | ➖ N/A            | N/A               | Some members will rest here                                                                                                                                 |

### Loop order

If any of these steps indicate that they've made changes to the board the entire loop will be run again.

1. Map email addresses to TidyHQ contacts
   * Looks for stories with the email address field set but no TidyHQ contact set. **Replaces** the email address field when we have a corresponding TidyHQ field.
   * Email addresses are mapped first so we don't duplicate stories that come from both email enquiries and TidyHQ sign ups.
1. Create new stories based on TidyHQ contacts
   Looks for any TidyHQ membership that is not expired and creates a prospective story if there's no story on the board with that TidyHQ ID set.
   This step is skipped if `--import` is **not** passed at runtime.
1. Use the column template to add tasks to newly created/progressed cards
   Adds template tasks for the current story status if they haven't been added before. Previous additions are tracked in `template_actions.json`.
1. Tick off tasks that can be closed by code
   Processing functions are mapped to task strings, renaming a task will break closing by code
1. Progress user stories based on task completion
   If all tasks on the current story are marked as complete progress the story to the next column. This checks against **all tasks** not just ones added by the current column template.
1. Tick off tasks that can be closed by story status
1. Progress stories based on TidyHQ sign up
   If a prospective/intake story has a TidyHQ ID set then that story has naturally progressed to the next stage outside of Taiga. Most of the close by code steps don't begin until status 3 so this has been added as a quality of life measure primarily for stories created by the TidyHQ sync step.
1. Progress stories based on AF membership
   If an attendee story has a membership progress them to the new member column

### Service load considerations

* TidyHQ - All results are accessed through a time cache (not just runtime) so queries to TidyHQ are reduced
* Taiga - Very little consideration has been put into reducing the number of calls to Taiga as the service is self hosted.

## Get Taiga token

Some of this code cannot easily refresh tokens so we can generate a long lived one using the code below (ran on the Taiga host)

* `./taiga-manage.sh shell`

```python
from datetime import timedelta
from taiga.users.models import User
from taiga.auth.tokens import AccessToken
AccessToken.lifetime = timedelta(days=365*100)
AccessToken.lifetime
user = User.objects.get(email="email@address")
token = AccessToken.for_user(user)
print(token)
```
