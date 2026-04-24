DROP TABLE IF EXISTS platform_whitelist CASCADE;

DROP TABLE IF EXISTS bot_configs CASCADE;

DROP TABLE IF EXISTS tattoo_bookings CASCADE;

DROP TABLE IF EXISTS tattoo_reviews CASCADE;

DROP TABLE IF EXISTS tattoo_portfolio CASCADE;

DROP TABLE IF EXISTS bot_subscriptions CASCADE;

DROP TABLE IF EXISTS applications CASCADE;

DROP TABLE IF EXISTS bot_blocked_users CASCADE;

DROP TABLE IF EXISTS jobs CASCADE;

DROP TABLE IF EXISTS registered_bots CASCADE;

DROP TABLE IF EXISTS users CASCADE;

DROP TYPE IF EXISTS tattoostyle CASCADE;

DROP TYPE IF EXISTS reviewstatus CASCADE;

DROP TYPE IF EXISTS bookingstatus CASCADE;

DROP TYPE IF EXISTS applicationstatus CASCADE;

DROP TYPE IF EXISTS jobstatus CASCADE;

DROP TYPE IF EXISTS jobtype CASCADE;

DROP TYPE IF EXISTS botniche CASCADE;

CREATE TYPE botniche AS ENUM ('LABOR', 'BEAUTY', 'SPORTS');

CREATE TYPE jobtype AS ENUM ('ONETIME', 'PERMANENT');

CREATE TYPE jobstatus AS ENUM ('OPEN', 'ASSIGNED', 'COMPLETED', 'CANCELLED');

CREATE TYPE reviewstatus AS ENUM ('PENDING', 'APPROVED', 'DELETED');

CREATE TYPE applicationstatus AS ENUM ('PENDING', 'ACCEPTED', 'REJECTED', 'COMPLETED', 'FAILED');

CREATE TYPE bookingstatus AS ENUM ('NEW', 'CANCELLED');

CREATE TABLE platform_whitelist (
	id SERIAL NOT NULL, 
	telegram_id BIGINT NOT NULL, 
	full_name VARCHAR(128), 
	username VARCHAR(64), 
	added_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_platform_whitelist PRIMARY KEY (id)
);

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

CREATE TABLE bot_configs (
	id SERIAL NOT NULL, 
	bot_id INTEGER NOT NULL, 
	key VARCHAR(64) NOT NULL, 
	value TEXT NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_bot_configs PRIMARY KEY (id), 
	CONSTRAINT uq_bot_config UNIQUE (bot_id, key), 
	CONSTRAINT fk_bot_configs_bot_id_registered_bots FOREIGN KEY(bot_id) REFERENCES registered_bots (id) ON DELETE CASCADE
);

CREATE TABLE bot_subscriptions (
	id SERIAL NOT NULL, 
	bot_id INTEGER NOT NULL, 
	telegram_id BIGINT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_bot_subscriptions PRIMARY KEY (id), 
	CONSTRAINT uq_bot_subscription UNIQUE (bot_id, telegram_id), 
	CONSTRAINT fk_bot_subscriptions_bot_id_registered_bots FOREIGN KEY(bot_id) REFERENCES registered_bots (id) ON DELETE CASCADE
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

CREATE TABLE tattoo_portfolio (
	id SERIAL NOT NULL, 
	bot_id INTEGER NOT NULL, 
	style VARCHAR(64) NOT NULL, 
	photo_id VARCHAR(256) NOT NULL, 
	description TEXT NOT NULL, 
	work_time VARCHAR(128) NOT NULL, 
	price VARCHAR(128) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_tattoo_portfolio PRIMARY KEY (id), 
	CONSTRAINT fk_tattoo_portfolio_bot_id_registered_bots FOREIGN KEY(bot_id) REFERENCES registered_bots (id) ON DELETE CASCADE
);

CREATE TABLE tattoo_reviews (
	id SERIAL NOT NULL, 
	bot_id INTEGER NOT NULL, 
	user_id BIGINT NOT NULL, 
	user_name VARCHAR(128), 
	text TEXT NOT NULL, 
	photo_id VARCHAR(256), 
	status reviewstatus NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_tattoo_reviews PRIMARY KEY (id), 
	CONSTRAINT fk_tattoo_reviews_bot_id_registered_bots FOREIGN KEY(bot_id) REFERENCES registered_bots (id) ON DELETE CASCADE
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

CREATE TABLE tattoo_bookings (
	id SERIAL NOT NULL, 
	bot_id INTEGER NOT NULL, 
	user_id BIGINT NOT NULL, 
	idea TEXT NOT NULL, 
	body_part VARCHAR(128) NOT NULL, 
	size VARCHAR(128) NOT NULL, 
	date VARCHAR(10) NOT NULL, 
	time_slot VARCHAR(5) NOT NULL, 
	phone VARCHAR(32), 
	reference_id INTEGER, 
	status bookingstatus NOT NULL, 
	cancel_reason TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_tattoo_bookings PRIMARY KEY (id), 
	CONSTRAINT fk_tattoo_bookings_bot_id_registered_bots FOREIGN KEY(bot_id) REFERENCES registered_bots (id) ON DELETE CASCADE, 
	CONSTRAINT fk_tattoo_bookings_reference_id_tattoo_portfolio FOREIGN KEY(reference_id) REFERENCES tattoo_portfolio (id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX ix_platform_whitelist_telegram_id ON platform_whitelist (telegram_id);

CREATE UNIQUE INDEX ix_registered_bots_bot_username ON registered_bots (bot_username);

CREATE INDEX ix_registered_bots_owner_telegram_id ON registered_bots (owner_telegram_id);

CREATE UNIQUE INDEX ix_registered_bots_token_hash ON registered_bots (token_hash);

CREATE INDEX ix_users_city ON users (city);

CREATE UNIQUE INDEX ix_users_telegram_id ON users (telegram_id);

CREATE INDEX ix_bot_blocked_users_telegram_id ON bot_blocked_users (telegram_id);

CREATE INDEX ix_bot_blocked_users_bot_id ON bot_blocked_users (bot_id);

CREATE INDEX ix_bot_configs_bot_id ON bot_configs (bot_id);

CREATE INDEX ix_bot_subscriptions_bot_id ON bot_subscriptions (bot_id);

CREATE INDEX ix_bot_subscriptions_telegram_id ON bot_subscriptions (telegram_id);

CREATE INDEX ix_jobs_status ON jobs (status);

CREATE INDEX ix_jobs_employer_telegram_id ON jobs (employer_telegram_id);

CREATE INDEX ix_jobs_bot_id ON jobs (bot_id);

CREATE INDEX ix_jobs_city_status ON jobs (city, status);

CREATE INDEX ix_portfolio_bot_style ON tattoo_portfolio (bot_id, style);

CREATE INDEX ix_tattoo_portfolio_bot_id ON tattoo_portfolio (bot_id);

CREATE INDEX ix_reviews_bot_status ON tattoo_reviews (bot_id, status);

CREATE INDEX ix_tattoo_reviews_bot_id ON tattoo_reviews (bot_id);

CREATE INDEX ix_applications_job_id ON applications (job_id);

CREATE INDEX ix_applications_worker_telegram_id ON applications (worker_telegram_id);

CREATE INDEX ix_bookings_bot_date ON tattoo_bookings (bot_id, date);

CREATE INDEX ix_tattoo_bookings_bot_id ON tattoo_bookings (bot_id);