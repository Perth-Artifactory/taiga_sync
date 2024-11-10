# Taiga sync

CRM-ish workflows tacked onto a kanban board innit

## Usage

* `--import` will create cards based on TidyHQ data.

## Tasks

| Task                            | Templated in  | Closed by code | Closed by status   | Removed by status  |
| ------------------------------- | ------------- | -------------- | ------------------ | ------------------ |
| Respond to query                | 1.Prospective | Req email proc | ✅2.Attendee       | 3.New Member       |
| Determine project viability     | 1.Prospective | Req email gpt  | N/A                | 4.Member (comp)    |
| Encourage to visit              | 1.Prospective | Req email gpt  | ✅2.Attendee       | 3.New Member       |
| Visit                           | 1.Prospective | N/A            | ✅2.Attendee       | 3.New Member       |
| Join Slack                      | 1.Prospective | ✅            | N/A code only       | 5.Keyholder (comp) |
| Participated in an event        | 2.Attendee    | N/A            | N/A                | 5.Keyholder        |
| Signed up as a visitor          | 2.Attendee    | ✅            | N/A                 | 3.New Member       |
| Discussed moving to membership  | 2.Attendee    | ✅            | N/A                 | 3.New Member       |
| Completed new visitor induction | 2.Attendee    | out of scope   | N/A                | 3.New Member       |
| Signed up as member             | 2.Attendee    | ✅            | ✅3.New Member     | 4.Member           |
| New member induction            | 3.New Member  | ✅            | N/A                 | N/A                |
| Planned first project           | 3.New Member  | N/A            | N/A                | N/A                |
| Attending events as a member    | 3.New Member  | N/A            | N/A                | 5.Keyholder        |
| Added to billing groups         | 3.New Member  | ✅            | N/A                 | N/A                |
| Demonstrated keyholder resp     | 4.Member      | N/A            | ✅5.Keyholder      | 6.Settled          |
| Offered key                     | 4.Member      | N/A            | ✅5.Keyholder      | 6.Settled          |
| Keyholder motion put to ManCom  | 5.Keyholder   | Req vote sync  | ✅6.Settled        | N/A                |
| Keyholder motion successful     | 5.Keyholder   | Req vote sync  | ✅6.Settled        | N/A                |
| Confirmed photo on tidyhq       | 5.Keyholder   | ✅            | ✅6.Settled        | N/A                |
| Confirmed paying via bank       | 5.Keyholder   | ✅            | ✅6.Settled        | N/A                |
| Send keyholder documentation    | 5.Keyholder   | ❌ tech limit | ✅6.Settled        | N/A                |
| Send bond invoice               | 5.Keyholder   | ✅            | ✅6.Settled        | N/A                |
| Keyholder induction completed   | 5.Keyholder   | out of scope   | ✅6.Settled        | N/A                |

## Loop order

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

## Service load considerations

* TidyHQ - All results are accessed through a time cache (not just runtime) so queries to TidyHQ are reduced
* Taiga - Very little consideration has been put into reducing the number of calls to Taiga as the service is self hosted.
