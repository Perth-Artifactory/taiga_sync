# Taiga sync

CRM-ish workflows tacked onto a kanban board innit

## Kanban/Taiga nomenclature

* Trello is another example of a **Kanban board** (we don't use it anymore because it's no longer free)
* Each card on a Kanban board is a **user story**
* Cards sit in one column at a time which is their **status**
* Things that need to be done for a user story are attached as **tasks**
* Some boards have a separate tab for **Issues** that we use primarily to track things reported from outside Taiga
* Some boards have a separate tab for **Epics**. These are used to track a collection of user stories into a large cohesive project.

## Issue syncing

Takes issues from a Slack channel and adds them to a project's issues. This typically requires a corresponding Slack workflow.

### Usage

`issue_sync.py`

* `--testing` will process all messages regardless of whether they've been processed before.

### Nomenclature

* **Issues** are things reported from a Slack workflow
* **User stories** are small scale projects
* **Epics** are collections of small scale projects that make up larger infrastructure builds. Not all user stories belong to an epic and not every project needs one.
* **Tasks** are individual things that can be done by a volunteer

### Statuses

| ID | Status           | Description |
| -- | ---------------- | ----------- |
| 1  | Reported         | Things reported from outside the slack workflow, primarily used for volunteers to jot down things |
| 2  | Triaged          | User stories that have had priorities set from the Reported status or have been triaged from a reported issue |
| 3  | Investigating    | Some amount of experienced investigation is required and a path set out for progression |
| 4  | Ready for action | Has tasks that are ready for people to complete |
| 5  | Resolving        | Moved from Ready for action once someone takes on the tasks that are ready for completion. Things that are being handled already should be here. |
| 6  | Resolved         | Once the problem has been solved |

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

| ID | Status                | Description                                         |
| -- | --------------------- | -------------------------------------------------- |
| 1  | Intake                | Where new email queries appear                     |
| 2  | Prospective           | Someone that might attend the space                |
| 3  | Attendee              | Day Pass attendees                                 |
| 4  | New Member            | People that have signed up as a member             |
| 5  | Member                | People that have started to "settle in" as members |
| 6  | Prospective Keyholder | People that should be considered for a key         |
| 7  | Keyholder             | People going through the keyholder process         |


### Tasks

Tasks are arranged so that once all tasks in a status are complete the attendee is considered a "good \<category\>"

| Task                            | Templated in       | Closed by code   | Closed by status      | Removed by status  | Position justification |
| ------------------------------- | ------------------ | ---------------- | --------------------- | ------------------ | ---------------------- |
| Respond to query                | 1.Intake           | ❔Req email proc | ✅3.Attendee         | 4.New Member       | The root status that user stories are created under when they start as an email query |
| Determine project viability     | 2.Prospective      | ❔Req email gpt  | ➖ N/A               | 5.Member (comp)    | Could conceivably be moved to 3.Attendee but this way we can mark the task as failed if it's clear from an enquiry email that we're not a suitable workshop |
| Encourage to visit              | 2.Prospective      | ❔Req email gpt  | ✅3.Attendee         | 4.New Member       | The primary target/goal for this status |
| Visit                           | 2.Prospective      | ➖ N/A          | ✅3.Attendee         | 4.New Member       | Primarily used to trigger the move to the next status |
| Join Slack                      | 3.Attendee         | ✅              | ➖ N/A               | N/A                | Slack sign up is typically promoted during the first visit. Until this happens it shouldn't be treated as a blocker for progression |
| Participated in an event        | 3.Attendee         | ➖ N/A          | ➖ N/A               | 7.Keyholder        | The primary target/goal for this status |
| Signed up as a visitor          | 3.Attendee         | ✅              | ➖ N/A               | 4.New Member       | Not required in the earlier statuses |
| Discussed moving to membership  | 3.Attendee         | ✅              | ➖ N/A               | 4.New Member       | Some attendees don't want to progress to membership, included here so we can indicate/track that |
| Completed new visitor induction | 3.Attendee         | ✅              | ➖ N/A               | 4.New Member       | A required step for attendees |
| Signed up as member             | 3.Attendee         | ✅              | ✅4.New Member       | 5.Member           | Here primarily to allow for programmatic progression |
| New member induction            | 4.New Member       | ✅              | ➖ N/A               | N/A                | A required step for new members |
| Planned first project           | 4.New Member       | ➖ N/A          | ➖ N/A               | N/A                | A key to success as a member. Tracked here instead of in 3.Attendee because we don't offer the same level of support with this to non members |
| Attending events as a member    | 4.New Member       | ➖ N/A          | ➖ N/A               | 6.Pros Keyholder   | Getting new members settled into events is key |
| Added to billing groups         | 4.New Member       | ✅              | ➖ N/A               | N/A                | A required bureaucratic step for new members |
| Received at least one induction | 4.New Member       | ✅              | ➖ N/A               | N/A                | TODO |
| TODO: split out key resp        | 5.Member           | ➖ N/A          | ✅7.Keyholder        | 8.Settled          | 
| Demonstrated keyholder resp     | 5.Member           | ➖ N/A          | ✅7.Keyholder        | 8.Settled          | Cannot be reasonable demonstrated until all the tasks in 4.New Member are complete. Some members will rest here. |
| Suggested key application/nom   | 5.Member           | ➖ N/A          | ✅7.Keyholder        | 8.Settled          | Tracking when a responsible person is willing to push for a key |
| Keyholder motion put to vote    | 6.Pros Keyholder   | ❔Req vote sync  | ✅7.Keyholder        | N/A                | A required bureaucratic step for keyholder applications |
| Keyholder motion successful     | 6.Pros Keyholder   | ❔Req vote sync  | ✅7.Keyholder        | N/A                | A required bureaucratic step for keyholder applications |
| Confirmed photo on tidyhq       | 6.Pros Keyholder   | ✅              | ➖ N/A               | N/A                | A required bureaucratic step for keyholder applications |
| Confirmed paying via bank       | 6.Pros Keyholder   | ✅              | ➖ N/A               | N/A                | A required bureaucratic step for keyholder applications |
| Send keyholder documentation    | 6.Pros Keyholder   | ❌ #4           | ✅7.Keyholder        | N/A                | A required bureaucratic step for keyholder applications |
| Send bond invoice               | 6.Pros Keyholder   | ✅              | ✅7.Keyholder        | N/A                | A required bureaucratic step for keyholder applications |
| Keyholder induction completed   | 6.Pros Keyholder   | ✅              | ✅7.Keyholder        | N/A                | A required bureaucratic step for keyholder applications |
| Discussed volunteering          | 7.Keyholder        | ➖ N/A          | ➖ N/A               | N/A                | Some members will rest here |

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
1. Progress tasks based on TidyHQ sign up
   If a prospective story has a TidyHQ ID set then that story has naturally progressed to the next stage outside of Taiga. Most of the close by code steps don't begin until status 2 so this has been added as a quality of life measure primarily for stories created by the TidyHQ sync step.

### Service load considerations

* TidyHQ - All results are accessed through a time cache (not just runtime) so queries to TidyHQ are reduced
* Taiga - Very little consideration has been put into reducing the number of calls to Taiga as the service is self hosted.
