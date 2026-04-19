DROP TABLE IF EXISTS applications CASCADE;

DROP TABLE IF EXISTS bot_blocked_users CASCADE;

DROP TABLE IF EXISTS jobs CASCADE;

DROP TABLE IF EXISTS registered_bots CASCADE;

DROP TABLE IF EXISTS users CASCADE;

DROP TYPE IF EXISTS applicationstatus CASCADE;

DROP TYPE IF EXISTS jobstatus CASCADE;

DROP TYPE IF EXISTS jobtype CASCADE;

DROP TYPE IF EXISTS botniche CASCADE;

CREATE TYPE botniche AS ENUM ('LABOR', 'BEAUTY', 'SPORTS');

CREATE TYPE jobtype AS ENUM ('ONETIME', 'PERMANENT');

CREATE TYPE jobstatus AS ENUM ('OPEN', 'ASSIGNED', 'COMPLETED', 'CANCELLED');

CREATE TYPE applicationstatus AS ENUM ('PENDING', 'ACCEPTED', 'REJECTED', 'COMPLETED', 'FAILED');

CREATE TABLE registered_bots (
	id SERIAL NOT NULL, 
	owner_telegram_id BIGINT NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	encrypted_token VARCHAR(512) NOT NULL, 
	bot_username VARCHAR(64) NOT NULL, 
	niche botniche NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_registered_bots PRIMARY KEY (id)
);

CREATE TABLE users (
	id SERIAL NOT NULL, 
	telegram_id BIGINT NOT NULL, 
	username VARCHAR(64), 
	first_name VARCHAR(128) NOT NULL, 
	last_name VARCHAR(128), 
	city VARCHAR(64), 
	global_rating FLOAT NOT NULL, 
	total_completed INTEGER NOT NULL, 
	total_failed INTEGER NOT NULL, 
	is_banned BOOLEAN NOT NULL, 
	terms_agreed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_users PRIMARY KEY (id)
);

CREATE TABLE bot_blocked_users (
	id SERIAL NOT NULL, 
	bot_id INTEGER NOT NULL, 
	telegram_id BIGINT NOT NULL, 
	blocked_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_bot_blocked_users PRIMARY KEY (id), 
	CONSTRAINT uq_blocked_bot_user UNIQUE (bot_id, telegram_id), 
	CONSTRAINT fk_bot_blocked_users_bot_id_registered_bots FOREIGN KEY(bot_id) REFERENCES registered_bots (id) ON DELETE CASCADE
);

CREATE TABLE jobs (
	id UUID NOT NULL, 
	bot_id INTEGER NOT NULL, 
	employer_telegram_id BIGINT NOT NULL, 
	job_type jobtype NOT NULL, 
	workers_needed INTEGER NOT NULL, 
	city VARCHAR(64) NOT NULL, 
	description TEXT NOT NULL, 
	pay_description VARCHAR(512) NOT NULL, 
	location VARCHAR(256) NOT NULL, 
	scheduled_time TIMESTAMP WITH TIME ZONE NOT NULL, 
	status jobstatus NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_jobs PRIMARY KEY (id), 
	CONSTRAINT fk_jobs_bot_id_registered_bots FOREIGN KEY(bot_id) REFERENCES registered_bots (id) ON DELETE CASCADE
);

CREATE TABLE applications (
	id SERIAL NOT NULL, 
	job_id UUID NOT NULL, 
	worker_telegram_id BIGINT NOT NULL, 
	status applicationstatus NOT NULL, 
	applied_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	confirmed_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_applications PRIMARY KEY (id), 
	CONSTRAINT uq_applications_job_worker UNIQUE (job_id, worker_telegram_id), 
	CONSTRAINT fk_applications_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX ix_registered_bots_token_hash ON registered_bots (token_hash);

CREATE INDEX ix_registered_bots_owner_telegram_id ON registered_bots (owner_telegram_id);

CREATE UNIQUE INDEX ix_registered_bots_bot_username ON registered_bots (bot_username);

CREATE INDEX ix_users_city ON users (city);

CREATE UNIQUE INDEX ix_users_telegram_id ON users (telegram_id);

CREATE INDEX ix_bot_blocked_users_telegram_id ON bot_blocked_users (telegram_id);

CREATE INDEX ix_bot_blocked_users_bot_id ON bot_blocked_users (bot_id);

CREATE INDEX ix_jobs_bot_id ON jobs (bot_id);

CREATE INDEX ix_jobs_status ON jobs (status);

CREATE INDEX ix_jobs_city_status ON jobs (city, status);

CREATE INDEX ix_jobs_employer_telegram_id ON jobs (employer_telegram_id);

CREATE INDEX ix_applications_worker_telegram_id ON applications (worker_telegram_id);

CREATE INDEX ix_applications_job_id ON applications (job_id);