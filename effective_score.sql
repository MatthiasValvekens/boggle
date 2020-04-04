-- compute max by submission and global max per round in one query
-- The rows where submission_id is null are the per round maxima
create or replace view max_valid_lengths as
    select 
        coalesce(max(word_len), 0) as max_word_len,
        session_id, -- player table
        round_no, -- submission table
        -- we could also use grouping(...) but it's nice to
        --  export the submission id anyway
        -- note that writing submission_id directly here is NOT an option, since it may be
        -- NULL if the submission does not have any valid words
        submission.id as submission_id
    from player
    join submission on player.id = submission.player_id
    left join (
        select length(word) as word_len, submission_id
        from word
        where (dictionary_valid and not duplicate and path_array is not null)
    ) as word_lens on submission.id = submission_id
    group by grouping sets ((session_id, round_no, submission.id), (session_id, round_no));

create or replace view effective_scores as
    select
        word.id as id, word.word as word, 
        submission_id, dictionary_valid, path_array, duplicate,
        (
            dictionary_valid 
                and path_array is not null 
                and not duplicate 
                and max_bonus.uniq 
                and length(word.word) = max_bonus.round_max
        ) as longest_bonus,
        (
            case 
                when (not dictionary_valid or path_array is null or duplicate) then 0
                when max_bonus.uniq and length(word.word) = max_bonus.round_max then (2 * score)
                else score
            end
        ) as score
    from word
    join submission on submission.id = word.submission_id
    join player on player.id = submission.player_id
    join (
        -- check if there is more than one submission in a given round
        --  achieving the maximal length using a clever self-join
        select
             session_id, round_no,
             max_word_len as round_max,
             -- check if the max value is unique
             (count(*) = 1) as uniq
        from max_valid_lengths sub_max
        -- we're only interested in the submissions that achieve the maximal length
        -- this boils down to joining on max_word_len and forcing the WHERE condition below
        join max_valid_lengths round_max using (session_id, round_no, max_word_len)
        where round_max.submission_id is null and sub_max.submission_id is not null
        -- the max_word_len is necessary to make the semantics checker happy
        -- but of course we know that it's uniquely determined by session_id and round_no
        group by (max_word_len, session_id, round_no)
    ) as max_bonus using (session_id, round_no);


create or replace view statistics as
    select player_id, round_no, coalesce(sum(word.score), 0) as total_score
    from effective_scores as word
    join submission on submission.id = word.submission_id
    join player on player.id = submission.player_id
    group by grouping sets ((player_id, round_no), (player_id));

