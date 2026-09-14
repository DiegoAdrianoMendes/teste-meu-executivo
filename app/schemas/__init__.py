from app.schemas.user import UserCreate, UserRead, AgentSummary, TokenResponse, LoginRequest
from app.schemas.client import ClientCreate, ClientRead
from app.schemas.matter import MatterCreate, MatterUpdate, MatterRead, MatterListItem, MatterMoveRequest
from app.schemas.task import TaskCreate, TaskUpdate, TaskRead
from app.schemas.document import DocumentCreate, DocumentRead, PresignedUrlResponse
from app.schemas.timeline_event import TimelineEventCreate, TimelineEventRead
from app.schemas.conversation import ConversationCreate, ConversationRead
from app.schemas.approval import ApprovalCreate, ApprovalResolveRequest, ApprovalRead
from app.schemas.notification import NotificationRead
from app.schemas.reports import KPIMetric, ReportsSummary, ChartDataPoint
