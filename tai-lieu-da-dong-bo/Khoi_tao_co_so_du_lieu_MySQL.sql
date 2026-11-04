-- =====================================================================
-- CSDL: HE THONG QUAN LY TRUYEN THONG DA KENH (Facebook, Instagram, TikTok)
-- DBMS : MySQL 8.0+ (InnoDB, utf8mb4)
-- Quy uoc:
--   * Thoi gian luu theo UTC (DATETIME). Mui gio hien thi lay tu workspaces.timezone
--   * PK dat ten <ten_bang_so_it>_id, kieu BIGINT UNSIGNED AUTO_INCREMENT
--   * Bang con phu thuoc workspace dung ON DELETE CASCADE; tham chieu toi users dung RESTRICT/SET NULL
--     (user duoc xoa mem bang status, khong xoa cung)
--   * Cac FK "kep" (xxx_id, workspace_id) bao dam du lieu khong bi tron giua cac workspace
-- =====================================================================

CREATE DATABASE IF NOT EXISTS social_media_mgmt_aligned
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE social_media_mgmt_aligned;

SET FOREIGN_KEY_CHECKS = 0;

-- =====================================================================
-- M1. TAI KHOAN VA PHAN QUYEN
-- =====================================================================

CREATE TABLE users (
  user_id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  email             VARCHAR(255)    NOT NULL,
  password_hash     VARCHAR(255)    NOT NULL,
  full_name         VARCHAR(150)    NOT NULL,
  avatar_url        VARCHAR(500)    NULL,
  status            ENUM('pending','active','locked','disabled') NOT NULL DEFAULT 'pending',
  email_verified_at DATETIME        NULL,
  last_login_at     DATETIME        NULL,
  created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id),
  UNIQUE KEY uq_users_email (email)
) ENGINE=InnoDB;

CREATE TABLE workspaces (
  workspace_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  name         VARCHAR(150)    NOT NULL,
  slug         VARCHAR(100)    NOT NULL,
  logo_url     VARCHAR(500)    NULL,
  timezone     VARCHAR(64)     NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
  owner_id     BIGINT UNSIGNED NOT NULL,
  created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at   DATETIME        NULL,
  PRIMARY KEY (workspace_id),
  UNIQUE KEY uq_workspaces_slug (slug),
  KEY idx_workspaces_owner (owner_id),
  CONSTRAINT fk_workspaces_owner FOREIGN KEY (owner_id) REFERENCES users (user_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE roles (
  role_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  code        VARCHAR(50)     NOT NULL,   -- admin, manager, editor, approver, viewer
  name        VARCHAR(100)    NOT NULL,
  description VARCHAR(255)    NULL,
  PRIMARY KEY (role_id),
  UNIQUE KEY uq_roles_code (code)
) ENGINE=InnoDB;

CREATE TABLE permissions (
  permission_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  code          VARCHAR(100)    NOT NULL,  -- vi du: post.create, post.approve
  description   VARCHAR(255)    NULL,
  PRIMARY KEY (permission_id),
  UNIQUE KEY uq_permissions_code (code)
) ENGINE=InnoDB;

CREATE TABLE role_permissions (
  role_id       BIGINT UNSIGNED NOT NULL,
  permission_id BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (role_id, permission_id),
  KEY idx_rp_permission (permission_id),
  CONSTRAINT fk_rp_role       FOREIGN KEY (role_id)       REFERENCES roles (role_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_rp_permission FOREIGN KEY (permission_id) REFERENCES permissions (permission_id)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE workspace_members (
  member_id    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  workspace_id BIGINT UNSIGNED NOT NULL,
  user_id      BIGINT UNSIGNED NOT NULL,
  role_id      BIGINT UNSIGNED NOT NULL,
  status       ENUM('active','suspended') NOT NULL DEFAULT 'active',
  joined_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (member_id),
  UNIQUE KEY uq_member_ws_user (workspace_id, user_id),
  KEY idx_member_user (user_id),
  KEY idx_member_role (role_id),
  CONSTRAINT fk_member_ws   FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_member_user FOREIGN KEY (user_id)      REFERENCES users (user_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_member_role FOREIGN KEY (role_id)      REFERENCES roles (role_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE workspace_invitations (
  invitation_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  workspace_id  BIGINT UNSIGNED NOT NULL,
  email         VARCHAR(255)    NOT NULL,
  role_id       BIGINT UNSIGNED NOT NULL,
  token_hash    CHAR(64)        NOT NULL,
  invited_by    BIGINT UNSIGNED NOT NULL,
  status        ENUM('pending','accepted','expired','revoked') NOT NULL DEFAULT 'pending',
  expires_at    DATETIME        NOT NULL,
  created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  accepted_at   DATETIME        NULL,
  PRIMARY KEY (invitation_id),
  UNIQUE KEY uq_invitation_token (token_hash),
  KEY idx_invitation_ws_email (workspace_id, email),
  KEY idx_invitation_role (role_id),
  KEY idx_invitation_inviter (invited_by),
  CONSTRAINT fk_inv_ws      FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_inv_role    FOREIGN KEY (role_id)      REFERENCES roles (role_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_inv_inviter FOREIGN KEY (invited_by)   REFERENCES users (user_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB;

-- =====================================================================
-- M2. QUAN LY KENH
-- =====================================================================

CREATE TABLE platforms (
  platform_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  code               VARCHAR(30)     NOT NULL,   -- facebook, instagram, tiktok
  name               VARCHAR(100)    NOT NULL,
  max_caption_length INT UNSIGNED    NULL,
  is_active          TINYINT(1)      NOT NULL DEFAULT 1,
  PRIMARY KEY (platform_id),
  UNIQUE KEY uq_platforms_code (code)
) ENGINE=InnoDB;

CREATE TABLE social_accounts (
  social_account_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  workspace_id        BIGINT UNSIGNED NOT NULL,
  platform_id         BIGINT UNSIGNED NOT NULL,
  external_account_id VARCHAR(100)    NOT NULL,   -- ID tren nen tang (page_id, ig_user_id, open_id)
  account_name        VARCHAR(255)    NOT NULL,
  account_type        ENUM('page','business','creator','profile') NOT NULL,
  avatar_url          VARCHAR(500)    NULL,
  linked_account_id   BIGINT UNSIGNED NULL,       -- IG Business lien ket voi Facebook Page
  status              ENUM('connected','expired','error','disconnected') NOT NULL DEFAULT 'connected',
  status_message      VARCHAR(500)    NULL,
  connected_by        BIGINT UNSIGNED NOT NULL,
  connected_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  disconnected_at     DATETIME        NULL,
  PRIMARY KEY (social_account_id),
  UNIQUE KEY uq_sa_ws_platform_ext (workspace_id, platform_id, external_account_id),
  UNIQUE KEY uq_sa_id_ws (social_account_id, workspace_id),   -- phuc vu FK kep
  KEY idx_sa_platform (platform_id),
  KEY idx_sa_linked (linked_account_id),
  KEY idx_sa_connected_by (connected_by),
  CONSTRAINT fk_sa_ws        FOREIGN KEY (workspace_id)      REFERENCES workspaces (workspace_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_sa_platform  FOREIGN KEY (platform_id)       REFERENCES platforms (platform_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_sa_linked    FOREIGN KEY (linked_account_id) REFERENCES social_accounts (social_account_id)
    ON DELETE SET NULL ON UPDATE RESTRICT,
  CONSTRAINT fk_sa_connected_by FOREIGN KEY (connected_by)   REFERENCES users (user_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB;

-- Quan he 1-1 voi social_accounts: token luu rieng de de ma hoa / phan quyen truy cap
CREATE TABLE social_account_tokens (
  token_id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  social_account_id  BIGINT UNSIGNED NOT NULL,
  access_token_enc   VARBINARY(2048) NOT NULL,   -- da ma hoa o tang ung dung
  refresh_token_enc  VARBINARY(2048) NULL,
  token_type         VARCHAR(30)     NULL,
  scopes             TEXT            NULL,
  expires_at         DATETIME        NULL,
  last_refreshed_at  DATETIME        NULL,
  refresh_fail_count INT UNSIGNED    NOT NULL DEFAULT 0,
  updated_at         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (token_id),
  UNIQUE KEY uq_sat_account (social_account_id),
  KEY idx_sat_expires (expires_at),
  CONSTRAINT fk_sat_account FOREIGN KEY (social_account_id) REFERENCES social_accounts (social_account_id)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB;

-- =====================================================================
-- M5. THU VIEN MEDIA (dat truoc M3 vi bai dang tham chieu toi media)
-- =====================================================================

CREATE TABLE media_folders (
  folder_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  workspace_id     BIGINT UNSIGNED NOT NULL,
  parent_folder_id BIGINT UNSIGNED NULL,
  name             VARCHAR(150)    NOT NULL,
  created_by       BIGINT UNSIGNED NOT NULL,
  created_at       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (folder_id),
  UNIQUE KEY uq_folder_id_ws (folder_id, workspace_id),
  KEY idx_folder_ws (workspace_id),
  KEY idx_folder_parent (parent_folder_id),
  KEY idx_folder_creator (created_by),
  CONSTRAINT fk_folder_ws      FOREIGN KEY (workspace_id)     REFERENCES workspaces (workspace_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_folder_parent  FOREIGN KEY (parent_folder_id) REFERENCES media_folders (folder_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_folder_creator FOREIGN KEY (created_by)       REFERENCES users (user_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE media_assets (
  media_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  workspace_id  BIGINT UNSIGNED NOT NULL,
  folder_id     BIGINT UNSIGNED NULL,
  uploaded_by   BIGINT UNSIGNED NOT NULL,
  file_name     VARCHAR(255)    NOT NULL,
  storage_key   VARCHAR(500)    NOT NULL,
  file_url      VARCHAR(500)    NOT NULL,
  thumbnail_url VARCHAR(500)    NULL,
  media_type    ENUM('image','video') NOT NULL,
  mime_type     VARCHAR(100)    NOT NULL,
  file_size     BIGINT UNSIGNED NOT NULL,
  width         INT UNSIGNED    NULL,
  height        INT UNSIGNED    NULL,
  duration_sec  DECIMAL(10,2)   NULL,
  created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at    DATETIME        NULL,
  PRIMARY KEY (media_id),
  UNIQUE KEY uq_media_id_ws (media_id, workspace_id),
  KEY idx_media_ws_type (workspace_id, media_type),
  KEY idx_media_folder (folder_id),
  KEY idx_media_uploader (uploaded_by),
  CONSTRAINT fk_media_ws       FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  -- FK kep: thu muc phai cung workspace voi media
  CONSTRAINT fk_media_folder   FOREIGN KEY (folder_id, workspace_id) REFERENCES media_folders (folder_id, workspace_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_media_uploader FOREIGN KEY (uploaded_by)  REFERENCES users (user_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE media_tags (
  tag_id       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  workspace_id BIGINT UNSIGNED NOT NULL,
  name         VARCHAR(100)    NOT NULL,
  PRIMARY KEY (tag_id),
  UNIQUE KEY uq_tag_ws_name (workspace_id, name),
  CONSTRAINT fk_tag_ws FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE media_asset_tags (
  media_id BIGINT UNSIGNED NOT NULL,
  tag_id   BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (media_id, tag_id),
  KEY idx_mat_tag (tag_id),
  CONSTRAINT fk_mat_media FOREIGN KEY (media_id) REFERENCES media_assets (media_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_mat_tag   FOREIGN KEY (tag_id)   REFERENCES media_tags (tag_id)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB;

-- =====================================================================
-- M3. SOAN VA DANG BAI
-- =====================================================================

-- Bai goc (soan 1 lan)
CREATE TABLE posts (
  post_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  workspace_id BIGINT UNSIGNED NOT NULL,
  created_by   BIGINT UNSIGNED NOT NULL,
  title        VARCHAR(255)    NULL,
  base_caption TEXT            NULL,
  status       ENUM('draft','pending_approval','approved','rejected','scheduled',
                    'publishing','published','partially_failed','failed','cancelled')
               NOT NULL DEFAULT 'draft',
  created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at   DATETIME        NULL,
  PRIMARY KEY (post_id),
  UNIQUE KEY uq_posts_id_ws (post_id, workspace_id),
  KEY idx_posts_ws_status (workspace_id, status),
  KEY idx_posts_creator (created_by),
  CONSTRAINT fk_posts_ws      FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_posts_creator FOREIGN KEY (created_by)   REFERENCES users (user_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB;

-- Noi dung rieng cho tung kenh (1 bai -> nhieu kenh)
CREATE TABLE post_variants (
  variant_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  post_id           BIGINT UNSIGNED NOT NULL,
  workspace_id      BIGINT UNSIGNED NOT NULL,
  social_account_id BIGINT UNSIGNED NOT NULL,
  caption           TEXT            NULL,
  platform_options  JSON            NULL,     -- tuy chon dac thu tung nen tang (privacy, reel/story...)
  status            ENUM('draft','scheduled','publishing','published','failed','cancelled')
                    NOT NULL DEFAULT 'draft',
  external_post_id  VARCHAR(100)    NULL,     -- ID bai tren nen tang sau khi dang
  external_post_url VARCHAR(500)    NULL,
  published_at      DATETIME        NULL,
  created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (variant_id),
  UNIQUE KEY uq_variant_post_account (post_id, social_account_id),
  UNIQUE KEY uq_variant_id_ws (variant_id, workspace_id),
  KEY idx_variant_account_status (social_account_id, status),
  KEY idx_variant_ws (workspace_id),
  KEY idx_variant_published (published_at),
  -- FK kep: bai va kenh deu phai thuoc cung workspace
  CONSTRAINT fk_variant_post    FOREIGN KEY (post_id, workspace_id)
    REFERENCES posts (post_id, workspace_id) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_variant_account FOREIGN KEY (social_account_id, workspace_id)
    REFERENCES social_accounts (social_account_id, workspace_id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB;

-- Anh/video dinh kem bai (dung chung cho cac kenh)
CREATE TABLE post_media (
  post_media_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  post_id       BIGINT UNSIGNED NOT NULL,
  media_id      BIGINT UNSIGNED NOT NULL,
  workspace_id  BIGINT UNSIGNED NOT NULL,
  sort_order    SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (post_media_id),
  UNIQUE KEY uq_pm_post_media (post_id, media_id),
  UNIQUE KEY uq_pm_post_order (post_id, sort_order),
  KEY idx_pm_media (media_id),
  CONSTRAINT fk_pm_post  FOREIGN KEY (post_id, workspace_id)
    REFERENCES posts (post_id, workspace_id) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_pm_media FOREIGN KEY (media_id, workspace_id)
    REFERENCES media_assets (media_id, workspace_id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB;

-- =====================================================================
-- M4. LEN LICH
-- =====================================================================

CREATE TABLE schedules (
  schedule_id  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  variant_id   BIGINT UNSIGNED NOT NULL,
  scheduled_at DATETIME        NOT NULL,          -- UTC
  status       ENUM('pending','queued','completed','cancelled') NOT NULL DEFAULT 'pending',
  created_by   BIGINT UNSIGNED NOT NULL,
  created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  cancelled_at DATETIME        NULL,
  PRIMARY KEY (schedule_id),
  KEY idx_sched_due (status, scheduled_at),       -- worker quet lich den han
  KEY idx_sched_variant (variant_id),
  KEY idx_sched_creator (created_by),
  CONSTRAINT fk_sched_variant FOREIGN KEY (variant_id) REFERENCES post_variants (variant_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_sched_creator FOREIGN KEY (created_by) REFERENCES users (user_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB;

-- Moi lan thu dang (ke ca dang ngay va retry) la 1 dong
CREATE TABLE publish_jobs (
  job_id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  variant_id       BIGINT UNSIGNED NOT NULL,
  schedule_id      BIGINT UNSIGNED NULL,          -- NULL neu "dang ngay"
  attempt_no       SMALLINT UNSIGNED NOT NULL DEFAULT 1,
  status           ENUM('queued','running','success','failed') NOT NULL DEFAULT 'queued',
  started_at       DATETIME        NULL,
  finished_at      DATETIME        NULL,
  next_retry_at    DATETIME        NULL,
  error_code       VARCHAR(100)    NULL,
  error_message    TEXT            NULL,
  response_payload JSON            NULL,
  created_at       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (job_id),
  UNIQUE KEY uq_job_variant_attempt (variant_id, attempt_no),
  KEY idx_job_status_retry (status, next_retry_at),
  KEY idx_job_schedule (schedule_id),
  CONSTRAINT fk_job_variant  FOREIGN KEY (variant_id)  REFERENCES post_variants (variant_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_job_schedule FOREIGN KEY (schedule_id) REFERENCES schedules (schedule_id)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB;

-- =====================================================================
-- M6. QUY TRINH DUYET BAI
-- =====================================================================

CREATE TABLE approval_requests (
  approval_id  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  post_id      BIGINT UNSIGNED NOT NULL,
  requested_by BIGINT UNSIGNED NOT NULL,
  request_note VARCHAR(500)    NULL,
  status       ENUM('pending','approved','rejected','cancelled') NOT NULL DEFAULT 'pending',
  resolved_by  BIGINT UNSIGNED NULL,
  reason       VARCHAR(1000)   NULL,             -- ly do duyet/tu choi
  requested_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at  DATETIME        NULL,
  PRIMARY KEY (approval_id),
  KEY idx_appr_post (post_id, status),
  KEY idx_appr_requester (requested_by),
  KEY idx_appr_resolver (resolved_by),
  CONSTRAINT fk_appr_post      FOREIGN KEY (post_id)      REFERENCES posts (post_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_appr_requester FOREIGN KEY (requested_by) REFERENCES users (user_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_appr_resolver  FOREIGN KEY (resolved_by)  REFERENCES users (user_id)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE post_comments (
  comment_id        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  post_id           BIGINT UNSIGNED NOT NULL,
  user_id           BIGINT UNSIGNED NOT NULL,
  parent_comment_id BIGINT UNSIGNED NULL,
  content           TEXT            NOT NULL,
  created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at        DATETIME        NULL,
  PRIMARY KEY (comment_id),
  KEY idx_pc_post (post_id, created_at),
  KEY idx_pc_user (user_id),
  KEY idx_pc_parent (parent_comment_id),
  CONSTRAINT fk_pc_post   FOREIGN KEY (post_id)           REFERENCES posts (post_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_pc_user   FOREIGN KEY (user_id)           REFERENCES users (user_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_pc_parent FOREIGN KEY (parent_comment_id) REFERENCES post_comments (comment_id)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE post_status_history (
  history_id  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  post_id     BIGINT UNSIGNED NOT NULL,
  from_status VARCHAR(30)     NULL,
  to_status   VARCHAR(30)     NOT NULL,
  changed_by  BIGINT UNSIGNED NULL,               -- NULL neu he thong tu doi (worker)
  note        VARCHAR(500)    NULL,
  changed_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (history_id),
  KEY idx_psh_post (post_id, changed_at),
  KEY idx_psh_user (changed_by),
  CONSTRAINT fk_psh_post FOREIGN KEY (post_id)    REFERENCES posts (post_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_psh_user FOREIGN KEY (changed_by) REFERENCES users (user_id)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB;

-- =====================================================================
-- M7. BAO CAO
-- =====================================================================

-- So lieu theo kenh, moi ngay 1 dong
CREATE TABLE account_daily_metrics (
  social_account_id BIGINT UNSIGNED NOT NULL,
  metric_date       DATE            NOT NULL,
  followers_count   BIGINT UNSIGNED NULL,
  followers_gain    INT             NULL,
  impressions       BIGINT UNSIGNED NULL,
  reach             BIGINT UNSIGNED NULL,
  video_views       BIGINT UNSIGNED NULL,
  engagements       BIGINT UNSIGNED NULL,
  profile_views     BIGINT UNSIGNED NULL,
  extra_metrics     JSON            NULL,
  synced_at         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (social_account_id, metric_date),
  CONSTRAINT fk_adm_account FOREIGN KEY (social_account_id) REFERENCES social_accounts (social_account_id)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB;

-- So lieu theo bai dang (moi lan dong bo la 1 snapshot)
CREATE TABLE post_metric_snapshots (
  snapshot_id  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  variant_id   BIGINT UNSIGNED NOT NULL,
  collected_at DATETIME        NOT NULL,
  impressions  BIGINT UNSIGNED NULL,
  reach        BIGINT UNSIGNED NULL,
  views        BIGINT UNSIGNED NULL,
  likes        BIGINT UNSIGNED NULL,
  comments     BIGINT UNSIGNED NULL,
  shares       BIGINT UNSIGNED NULL,
  saves        BIGINT UNSIGNED NULL,
  clicks       BIGINT UNSIGNED NULL,
  extra_metrics JSON           NULL,
  PRIMARY KEY (snapshot_id),
  UNIQUE KEY uq_pms_variant_time (variant_id, collected_at),
  CONSTRAINT fk_pms_variant FOREIGN KEY (variant_id) REFERENCES post_variants (variant_id)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE report_exports (
  export_id    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  workspace_id BIGINT UNSIGNED NOT NULL,
  created_by   BIGINT UNSIGNED NOT NULL,
  report_type  VARCHAR(50)     NOT NULL,          -- channel_overview, post_performance...
  file_format  ENUM('pdf','xlsx','csv') NOT NULL,
  params       JSON            NULL,              -- khoang ngay, kenh loc...
  status       ENUM('pending','done','failed') NOT NULL DEFAULT 'pending',
  file_url     VARCHAR(500)    NULL,
  created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at  DATETIME        NULL,
  PRIMARY KEY (export_id),
  KEY idx_re_ws (workspace_id, created_at),
  KEY idx_re_user (created_by),
  CONSTRAINT fk_re_ws   FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_re_user FOREIGN KEY (created_by)   REFERENCES users (user_id)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB;

-- =====================================================================
-- M8. TUONG TAC (comment / tin nhan)
-- =====================================================================

CREATE TABLE conversations (
  conversation_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  workspace_id      BIGINT UNSIGNED NOT NULL,
  social_account_id BIGINT UNSIGNED NOT NULL,
  variant_id        BIGINT UNSIGNED NULL,          -- co gia tri neu la binh luan duoi 1 bai
  conversation_type ENUM('comment_thread','direct_message') NOT NULL,
  external_id       VARCHAR(150)    NOT NULL,      -- ID luong tren nen tang
  participant_name  VARCHAR(255)    NULL,
  participant_external_id VARCHAR(100) NULL,
  status            ENUM('open','resolved','spam') NOT NULL DEFAULT 'open',
  assigned_to       BIGINT UNSIGNED NULL,
  last_message_at   DATETIME        NULL,
  created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (conversation_id),
  UNIQUE KEY uq_conv_account_ext (social_account_id, external_id),
  KEY idx_conv_ws_status (workspace_id, status, last_message_at),
  KEY idx_conv_variant (variant_id),
  KEY idx_conv_assignee (assigned_to),
  CONSTRAINT fk_conv_account  FOREIGN KEY (social_account_id, workspace_id)
    REFERENCES social_accounts (social_account_id, workspace_id) ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_conv_variant  FOREIGN KEY (variant_id)  REFERENCES post_variants (variant_id)
    ON DELETE SET NULL ON UPDATE RESTRICT,
  CONSTRAINT fk_conv_assignee FOREIGN KEY (assigned_to) REFERENCES users (user_id)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE messages (
  message_id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  conversation_id     BIGINT UNSIGNED NOT NULL,
  external_message_id VARCHAR(150)    NULL,
  direction           ENUM('inbound','outbound') NOT NULL,
  sender_name         VARCHAR(255)    NULL,
  sent_by_user_id     BIGINT UNSIGNED NULL,        -- nhan vien tra loi (chi co khi outbound)
  content             TEXT            NULL,
  attachments         JSON            NULL,
  sent_at             DATETIME        NOT NULL,
  created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (message_id),
  UNIQUE KEY uq_msg_conv_ext (conversation_id, external_message_id),
  KEY idx_msg_conv_time (conversation_id, sent_at),
  KEY idx_msg_sender (sent_by_user_id),
  CONSTRAINT fk_msg_conv FOREIGN KEY (conversation_id)  REFERENCES conversations (conversation_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_msg_user FOREIGN KEY (sent_by_user_id)  REFERENCES users (user_id)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB;

-- =====================================================================
-- M9. THONG BAO
-- =====================================================================

CREATE TABLE notifications (
  notification_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id         BIGINT UNSIGNED NOT NULL,        -- nguoi nhan
  workspace_id    BIGINT UNSIGNED NULL,
  type            VARCHAR(50)     NOT NULL,        -- post_published, post_failed, approval_requested...
  title           VARCHAR(255)    NOT NULL,
  body            VARCHAR(1000)   NULL,
  entity_type     VARCHAR(50)     NULL,            -- post, approval, social_account...
  entity_id       BIGINT UNSIGNED NULL,
  is_read         TINYINT(1)      NOT NULL DEFAULT 0,
  read_at         DATETIME        NULL,
  email_status    ENUM('not_required','pending','sent','failed') NOT NULL DEFAULT 'not_required',
  email_sent_at   DATETIME        NULL,
  created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (notification_id),
  KEY idx_noti_user_unread (user_id, is_read, created_at),
  KEY idx_noti_ws (workspace_id),
  KEY idx_noti_email (email_status),
  CONSTRAINT fk_noti_user FOREIGN KEY (user_id)      REFERENCES users (user_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_noti_ws   FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE notification_settings (
  user_id        BIGINT UNSIGNED NOT NULL,
  workspace_id   BIGINT UNSIGNED NOT NULL,
  type           VARCHAR(50)     NOT NULL,
  in_app_enabled TINYINT(1)      NOT NULL DEFAULT 1,
  email_enabled  TINYINT(1)      NOT NULL DEFAULT 1,
  PRIMARY KEY (user_id, workspace_id, type),
  KEY idx_ns_ws (workspace_id),
  CONSTRAINT fk_ns_user FOREIGN KEY (user_id)      REFERENCES users (user_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_ns_ws   FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB;

-- =====================================================================
-- M10. QUAN TRI HE THONG
-- =====================================================================

CREATE TABLE audit_logs (
  audit_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  workspace_id BIGINT UNSIGNED NULL,
  user_id      BIGINT UNSIGNED NULL,               -- NULL neu do he thong thuc hien
  action       VARCHAR(100)    NOT NULL,           -- post.create, channel.disconnect, member.role_change...
  entity_type  VARCHAR(50)     NOT NULL,
  entity_id    BIGINT UNSIGNED NULL,
  old_values   JSON            NULL,
  new_values   JSON            NULL,
  ip_address   VARCHAR(45)     NULL,
  user_agent   VARCHAR(500)    NULL,
  created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (audit_id),
  KEY idx_audit_ws_time (workspace_id, created_at),
  KEY idx_audit_user (user_id),
  KEY idx_audit_entity (entity_type, entity_id),
  -- Giu lai nhat ky ke ca khi workspace/user bi xoa
  CONSTRAINT fk_audit_ws   FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
    ON DELETE SET NULL ON UPDATE RESTRICT,
  CONSTRAINT fk_audit_user FOREIGN KEY (user_id)      REFERENCES users (user_id)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB;

-- Cau hinh toan he thong
CREATE TABLE system_settings (
  setting_key   VARCHAR(100)    NOT NULL,
  setting_value JSON            NOT NULL,
  description   VARCHAR(255)    NULL,
  updated_by    BIGINT UNSIGNED NULL,
  updated_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (setting_key),
  KEY idx_ss_updater (updated_by),
  CONSTRAINT fk_ss_user FOREIGN KEY (updated_by) REFERENCES users (user_id)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB;

-- Cau hinh rieng tung workspace
CREATE TABLE workspace_settings (
  workspace_id  BIGINT UNSIGNED NOT NULL,
  setting_key   VARCHAR(100)    NOT NULL,
  setting_value JSON            NOT NULL,
  updated_by    BIGINT UNSIGNED NULL,
  updated_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (workspace_id, setting_key),
  KEY idx_wss_updater (updated_by),
  CONSTRAINT fk_wss_ws   FOREIGN KEY (workspace_id) REFERENCES workspaces (workspace_id)
    ON DELETE CASCADE ON UPDATE RESTRICT,
  CONSTRAINT fk_wss_user FOREIGN KEY (updated_by)   REFERENCES users (user_id)
    ON DELETE SET NULL ON UPDATE RESTRICT
) ENGINE=InnoDB;


-- ALIGNED 2026-11-03. Fresh-install schema in a separate database; not a migration.
ALTER TABLE users
  ADD failed_login_attempts INT UNSIGNED NOT NULL DEFAULT 0,
  ADD locked_until DATETIME NULL,
  ADD is_system_admin TINYINT(1) NOT NULL DEFAULT 0;

CREATE TABLE auth_sessions (
  session_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT UNSIGNED NOT NULL,
  token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at DATETIME NOT NULL,
  revoked_at DATETIME NULL,
  UNIQUE KEY uq_session_token (token_hash),
  KEY idx_session_user_expiry (user_id, expires_at),
  CONSTRAINT fk_session_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE auth_action_tokens (
  action_token_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT UNSIGNED NOT NULL,
  purpose ENUM('verify_email','password_reset') NOT NULL,
  token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  expires_at DATETIME NOT NULL,
  consumed_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_action_token (token_hash),
  KEY idx_action_user_purpose (user_id, purpose, expires_at),
  CONSTRAINT fk_action_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- NULL values may repeat; only active/pending/successful rows consume the unique slot.
ALTER TABLE schedules
  ADD active_variant_id BIGINT UNSIGNED GENERATED ALWAYS AS
    (CASE WHEN status IN ('pending','queued') THEN variant_id ELSE NULL END) STORED,
  ADD UNIQUE KEY uq_sched_active_variant (active_variant_id),
  ADD UNIQUE KEY uq_schedule_id_variant (schedule_id, variant_id);

ALTER TABLE approval_requests
  ADD pending_post_id BIGINT UNSIGNED GENERATED ALWAYS AS
    (CASE WHEN status = 'pending' THEN post_id ELSE NULL END) STORED,
  ADD UNIQUE KEY uq_approval_pending_post (pending_post_id);

ALTER TABLE publish_jobs
  ADD successful_variant_id BIGINT UNSIGNED GENERATED ALWAYS AS
    (CASE WHEN status = 'success' THEN variant_id ELSE NULL END) STORED,
  ADD UNIQUE KEY uq_job_success_variant (successful_variant_id),
  ADD claim_token CHAR(36) NULL,
  ADD lease_expires_at DATETIME NULL,
  ADD KEY idx_job_lease (status, lease_expires_at),
  DROP FOREIGN KEY fk_job_schedule,
  ADD CONSTRAINT fk_job_schedule_variant FOREIGN KEY (schedule_id, variant_id)
    REFERENCES schedules(schedule_id, variant_id) ON DELETE RESTRICT ON UPDATE RESTRICT;
-- Retain schedules with jobs for history; cancel them instead of deleting them.
-- Unique success protects local records only. Remote idempotency/reconciliation is mandatory.

ALTER TABLE notifications
  ADD email_attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
  ADD email_next_retry_at DATETIME NULL,
  ADD email_last_error VARCHAR(1000) NULL,
  ADD in_app_visible TINYINT(1) NOT NULL DEFAULT 1,
  ADD email_claim_token CHAR(36) NULL,
  ADD email_lease_expires_at DATETIME NULL;
-- Email-only notifications use in_app_visible=0 and must be excluded from unread counts.

SET FOREIGN_KEY_CHECKS = 1;

-- =====================================================================
-- DU LIEU KHOI TAO
-- =====================================================================

INSERT INTO platforms (code, name, max_caption_length) VALUES
  ('facebook',  'Facebook',  63206),
  ('instagram', 'Instagram', 2200),
  ('tiktok',    'TikTok',    2200);

INSERT INTO roles (code, name, description) VALUES
  ('admin',    'Admin',        'Toan quyen trong workspace'),
  ('manager',  'Quan ly',      'Quan ly kenh, noi dung, thanh vien'),
  ('editor',   'Bien tap',     'Soan va len lich bai'),
  ('approver', 'Nguoi duyet',  'Duyet/tu choi bai dang'),
  ('viewer',   'Chi xem',      'Chi xem noi dung va bao cao');

INSERT INTO permissions (code, description) VALUES
  ('workspace.manage', 'Quan ly workspace'),
  ('member.manage',    'Moi, xoa, doi vai tro thanh vien'),
  ('channel.manage',   'Ket noi/ngat ket noi kenh'),
  ('post.create',      'Tao bai dang'),
  ('post.edit',        'Sua/len lich bai dang'),
  ('post.publish',     'Dang bai'),
  ('post.approve',     'Duyet/tu choi bai'),
  ('media.manage',     'Quan ly thu vien media'),
  ('report.view',      'Xem bao cao'),
  ('report.export',    'Xuat bao cao'),
  ('inbox.reply',      'Tra loi comment/tin nhan'),
  ('audit.view',       'Xem nhat ky hoat dong');

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.role_id, p.permission_id
FROM roles r
JOIN permissions p
WHERE r.code = 'admin'
   OR (r.code = 'manager'  AND p.code <> 'workspace.manage')
   OR (r.code = 'editor'   AND p.code IN ('post.create','post.edit','media.manage','report.view','inbox.reply'))
   OR (r.code = 'approver' AND p.code IN ('post.approve','post.publish','report.view'))
   OR (r.code = 'viewer'   AND p.code IN ('report.view'));
INSERT INTO system_settings (setting_key, setting_value, description) VALUES
 ('auth.lock_minutes', '15', 'Temporary login lock after five failed attempts'),
 ('auth.idle_minutes', '30', 'Session idle timeout'),
 ('auth.session_max_days', '7', 'Absolute session lifetime'),
 ('auth.verify_email_hours', '24', 'Email verification token lifetime'),
 ('auth.password_reset_minutes', '30', 'Password reset token lifetime'),
 ('publish.max_retries', '3', 'Three retries after initial attempt'),
 ('publish.retry_delays_seconds', '[60,120,240]', 'Retry delay defaults');
-- On workspace creation, in the same transaction as owner membership:
-- INSERT workspace_settings require_approval=false, allow_self_approval=false.
-- Configure the database connection/session time_zone='+00:00' in application setup.
