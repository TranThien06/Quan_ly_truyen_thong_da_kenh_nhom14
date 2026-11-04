-- Durable state required by the API/worker. Apply using python -m scripts.migrate.
CREATE TABLE oauth_states (
  state_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
  workspace_id BIGINT UNSIGNED NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  platform_code VARCHAR(30) NOT NULL,
  verifier_enc VARBINARY(2048) NULL,
  expires_at DATETIME NOT NULL,
  consumed_at DATETIME NULL,
  FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE email_outbox (
  email_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  recipient VARCHAR(255) NOT NULL,
  subject VARCHAR(255) NOT NULL,
  body_enc MEDIUMBLOB NOT NULL,
  status ENUM('pending','sending','sent','failed') NOT NULL DEFAULT 'pending',
  attempt_count INT NOT NULL DEFAULT 0,
  next_attempt_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  lease_expires_at DATETIME NULL,
  claim_token CHAR(36) NULL,
  last_error VARCHAR(100) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_outbox_due (status,next_attempt_at)
) ENGINE=InnoDB;

CREATE TABLE oauth_connections (
  connection_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
  workspace_id BIGINT UNSIGNED NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  accounts_enc MEDIUMBLOB NOT NULL,
  expires_at DATETIME NOT NULL,
  FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

ALTER TABLE publish_jobs
  ADD needs_reconciliation TINYINT(1) NOT NULL DEFAULT 0,
  ADD retry_count INT NOT NULL DEFAULT 0,
  ADD requested_by BIGINT UNSIGNED NULL;
ALTER TABLE report_exports
  MODIFY status ENUM('pending','running','done','failed') NOT NULL DEFAULT 'pending',
  ADD claim_token CHAR(36) NULL,
  ADD lease_expires_at DATETIME NULL;
ALTER TABLE social_accounts
  ADD last_metrics_sync_at DATETIME NULL,
  ADD last_inbox_sync_at DATETIME NULL;

CREATE TABLE account_deletion_requests (
  request_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT UNSIGNED NOT NULL,
  status ENUM('pending','completed') NOT NULL DEFAULT 'pending',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE outbound_requests (
  request_id CHAR(36) NOT NULL,
  conversation_id BIGINT UNSIGNED NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  content TEXT NOT NULL,
  status ENUM('sending','sent','unknown') NOT NULL,
  message_id BIGINT UNSIGNED NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(conversation_id,request_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE metric_sync_points (
  variant_id BIGINT UNSIGNED NOT NULL,
  checkpoint_hours INT NOT NULL,
  PRIMARY KEY(variant_id,checkpoint_hours),
  FOREIGN KEY(variant_id) REFERENCES post_variants(variant_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE webhook_receipts (
  payload_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
  received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE api_idempotency (
  workspace_id BIGINT UNSIGNED NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  operation VARCHAR(50) NOT NULL,
  request_key VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  body_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  resource_id BIGINT UNSIGNED NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(workspace_id,user_id,operation,request_key),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE
) ENGINE=InnoDB;
