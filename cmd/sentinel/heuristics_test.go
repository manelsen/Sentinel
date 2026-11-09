package main

import (
	"fmt"
	"testing"
)

func TestHostileWindowScoresHigh(t *testing.T) {
	service := &Service{cfg: defaultConfig()}
	rows := make([]windowRow, 0, 4)
	for i, author := range []string{"a", "b", "a", "b"} {
		idx := i + 1
		var replyTo *string
		if idx > 1 {
			prev := fmt.Sprintf("m%d", idx-1)
			replyTo = &prev
		}
		features := computeMessageFeatures(
			"VOCE esta distorcendo tudo de novo!!!",
			replyTo,
			false,
			true,
		)
		rows = append(rows, windowRow{
			MessageID:      fmt.Sprintf("m%d", idx),
			UserID:         author,
			AuthorName:     author,
			MessageType:    "text",
			SortTS:         parseTimestamp(fmt.Sprintf("2026-04-18T16:00:0%dZ", idx)),
			AnalysisText:   "VOCE esta distorcendo tudo de novo!!!",
			ReplyTo:        replyTo,
			CapsRatio:      features.CapsRatio,
			ExclamationCnt: features.ExclamationCount,
			QuestionCnt:    features.QuestionCount,
			DirectAttack:   features.DirectAttack,
			Profanity:      features.Profanity,
			Negativity:     features.Negativity,
		})
	}

	window := service.computeWindowFeatures(rows)
	if window.Risk <= 0.55 {
		t.Fatalf("expected risk > 0.55, got %.4f", window.Risk)
	}
	if severityFromScore(window.Risk) != "tensao" {
		t.Fatalf("expected severity tensao, got %s", severityFromScore(window.Risk))
	}
}
