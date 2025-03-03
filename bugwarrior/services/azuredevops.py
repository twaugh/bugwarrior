from bugwarrior.config import die
from bugwarrior.services import IssueService, Issue, ServiceClient
from urllib.parse import quote
import base64
import requests
import re


def striphtml(data):
    p = re.compile(r"<.*?>")
    return p.sub("", data)

def format_item(item):
    """Removes HTML Elements, splits by line"""
    if item:
        item_lines = re.split(r"<br>|</.*?>|&nbsp;", item)
        text = "\n".join(
            [striphtml(line) for line in item_lines if striphtml(line)]
        )
        return text
    return

class AzureDevopsClient(ServiceClient):
    def __init__(self, pat, org, project, host):
        if pat[0] != ":":
            self.pat = f":{pat}"
        else:
            self.pat = pat
        self.token = base64.b64encode(self.pat.encode("ascii")).decode("ascii")
        self.organization = quote(org)
        self.project = quote(project)
        self.host = host
        self.base_url = f"https://{host}/{org}/{project}/_apis/wit"
        self.session = requests.Session()
        self.session.headers = {
            "authorization": f"Basic {self.token}",
            "accept": "application/json",
            "content-type": "application/json",
        }
        self.params = {"api-version": "6.0-preview.2"}

    def get_work_item(self, workitemid):
        queryset = self.params.copy()
        queryset.update({"$expand": "all"})
        resp = self.session.get(f"{self.base_url}/workitems/{workitemid}", params=queryset)
        return resp.json()

    def get_work_items_from_query(self, query):
        data = str({"query": query})
        resp = self.session.post(f"{self.base_url}/wiql", data=data, params=self.params)
        if resp.status_code == 400 and resp.json()['typeKey'] == "WorkItemTrackingQueryResultSizeLimitExceededException":
            die("Too many azure devops results in query, please narrow the search by updating the ado.wiql_filter")
        return [workitem['id'] for workitem in resp.json()["workItems"]]

    def get_workitem_comments(self, workitem):
        comment_link = workitem["_links"]["workItemComments"]["href"]
        resp = self.session.get(comment_link)
        return resp.json().get("comments", None)

    def get_parent_name(self, workitem):
        parent_id = workitem.get("fields").get("System.Parent", None)
        if parent_id:
            parent_item = self.get_work_item(parent_id)
            return parent_item["fields"]["System.Title"]
        else:
            return None

class AzureDevopsIssue(Issue):
    TITLE = "adotitle"
    DESCRIPTION = "adodescription"
    ID = "adoid"
    URL = "adourl"
    TYPE = "adotype"
    STATE = "adostate"
    ACTIVITY = "adoactivity"
    PRIORITY = "adopriority"
    REMAINING_WORK = "adoremainingwork"
    PARENT = "adoparent"
    NAMESPACE = "adonamespace"

    UDAS = {
        TITLE: {"type": "string", "label": "Azure Devops Title"},
        DESCRIPTION: {"type": "string", "label": "Azure Devops Description"},
        ID: {"type": "numeric", "label": "Azure Devops ID number"},
        URL: {"type": "string", "label": "Azure Devops URL"},
        TYPE: {"type": "string", "label": "Azure Devops Work Item Type"},
        STATE: {"type": "string", "label": "Azure Devops Work Item State"},
        ACTIVITY: {"type": "string", "label": "Azure Devops Activity"},
        PRIORITY: {"type": "numeric", "label": "Azure Devops Priority"},
        REMAINING_WORK: {
            "type": "numeric",
            "label": "Azure Devops Amount of Remaining Work",
        },
        PARENT: {"type": "string", "label": "Azure Devops Parent Work Item Name"},
        NAMESPACE: {"type": "string", "label": "Azure Devops Namespace"},
    }
    UNIQUE_KEY = (URL,)

    PRIORITY_MAP = {"1": "H", "2": "M", "3": "L", "4": "L"}

    def get_priority(self):
        value = self.record.get("fields").get(
                "Microsoft.VSTS.Common.Priority", self.origin['default_priority']
            )
        return self.PRIORITY_MAP.get(value, self.origin['default_priority'])


    def to_taskwarrior(self):
        return {
            "project": self.extra['project'],
            "priority": self.get_priority(),
            "annotations": self.extra.get("annotations", []),
            "entry": self.parse_date(
                self.record.get("fields", {}).get("System.CreatedDate")
            ),
            "end": self.parse_date(
                self.record.get("fields",{}).get("Microsoft.VSTS.Common.ClosedDate")
            ),
            self.TITLE: self.record["fields"]["System.Title"],
            self.DESCRIPTION: format_item(
                self.record.get("fields").get("System.Description")
            ),
            self.ID: self.record["id"],
            self.URL: self.get_processed_url(self.record["_links"]["html"]["href"]),
            self.TYPE: self.record["fields"]["System.WorkItemType"],
            self.STATE: self.record["fields"]["System.State"],
            self.ACTIVITY: self.record.get("fields").get("System.Activity", ""),
            self.PRIORITY: self.record.get("fields").get(
                "Microsoft.VSTS.Common.Priority", self.origin['default_priority']
            ),
            self.REMAINING_WORK: self.record.get("fields").get(
                "Microsoft.VSTS.Scheduling.RemainingWork"
            ),
            self.PARENT: self.record.get("ParentTitle"),
            self.NAMESPACE: self.extra.get("namespace"),
        }

    def get_default_description(self):
        return self.build_default_description(
            title=self.record["fields"]["System.Title"],
            url=self.get_processed_url(self.record["_links"]["html"]["href"]),
            number=self.record["id"],
            cls=self.record["fields"]["System.WorkItemType"].lower(),
        )


class AzureDevopsService(IssueService):
    ISSUE_CLASS = AzureDevopsIssue
    CONFIG_PREFIX = "ado"

    def __init__(self, *args, **kw):
        super(AzureDevopsService, self).__init__(*args, **kw)
        self.host = self.config.get("host", "dev.azure.com")
        self.PAT = self.config.get("PAT")
        self.project = self.config.get("project")
        self.org = self.config.get("organization")
        self.client = AzureDevopsClient(
            pat=self.PAT, project=quote(self.project), org=quote(self.org), host=self.host
        )
        self.query_filter = self.config.get("wiql_filter")

    def get_query(self):
        default_query = "SELECT [System.Id] FROM workitems"

        # Test for Clauses, add WHERE if any exist
        if any([self.query_filter, self.config.get("only_if_assigned"), self.config.get("also_unassigned")]):
            default_query += " WHERE "
        
        # Adding The User Added Query
        if self.query_filter:
            default_query += self.query_filter

        # Adding logic for common configuration items
        if self.config.get("only_if_assigned"):
            if self.query_filter:
                    default_query += " AND "
            if self.config.get("also_unassigned"):
                default_query += "([System.AssignedTo] = @me OR [System.AssignedTo] == '')"
            else:
                default_query += "[System.AssignedTo] = @me "
        
        list_of_items = self.client.get_work_items_from_query(default_query)
        return list_of_items

    def annotations(self, issue, issue_obj):
        # Build Annotations based on comments by commenter and comment text
        url = issue["_links"]["html"]["href"]
        annotations = []
        if self.annotation_comments:
            comments = self.client.get_workitem_comments(issue)
            if comments:
                for comment in comments:
                    try:
                        name = comment["revisedBy"]["displayName"]
                    except KeyError:
                        name = comment["modifiedBy"]["displayName"]
                    text = format_item(comment["text"])
                    annotations.append((name, text))
        return self.build_annotations(annotations, issue_obj.get_processed_url(url))

    def issues(self):
        issue_ids = self.get_query()
        for issue_id in issue_ids:
            issue = self.client.get_work_item(issue_id)
            parent_title = self.client.get_parent_name(issue)
            issue["ParentTitle"] = parent_title
            issue_obj = self.get_issue_for_record(issue)
            extra = {
                "project": issue["ParentTitle"],
                "annotations": self.annotations(issue, issue_obj),
                "namespace": f"{self.org}\\{self.project}",
            }
            issue_obj.update_extra(extra)
            yield issue_obj

    @classmethod
    def validate_config(cls, service_config, target):
        for option in ("PAT", "project", "organization"):
            if option not in service_config:
                die(f"[{target}] has no 'ado.{option}'")
        super(AzureDevopsService, cls).validate_config(service_config, target)

    def get_owner(self, issue):
        # Issue filtering is implemented as part of issue aggregation.
        pass
