-- creates table for storing suspected edits
create table copyright_diffs(id int(10) unsigned not null auto_increment primary key, project varchar(20) not null, lang varbinary(20) not null, diff int(10) unsigned not null, diff_timestamp binary(14) not null, page_title varbinary(255) not null, page_ns int(11) not null, ithenticate_id int(11) not null, report blob, status varbinary(255), status_user varbinary(255), review_timestamp datetime default null);
create index copyright_page_idx on copyright_diffs(project, lang, page_title, page_ns);
create index copyright_time_idx on copyright_diffs(project, lang, diff_timestamp);

create unique index diff_idx on copyright_diffs(project, lang, diff);
