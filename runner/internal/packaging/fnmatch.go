package packaging

// Glob segments follow the coordinator's Python fnmatchcase grammar. In
// particular, ! negates only at the start of a complete class, [ is literal
// inside a class, and hyphens at either end are literal. Go path.Match uses a
// different grammar, so converting individual marker characters is insufficient.
type globRuneRange struct{ low, high rune }
type globToken struct {
	kind    rune
	literal rune
	negated bool
	ranges  []globRuneRange
}

func parseGlobSegment(pattern string) []globToken {
	characters := []rune(pattern)
	var tokens []globToken
	for i := 0; i < len(characters); {
		char := characters[i]
		i++
		switch char {
		case '*':
			if len(tokens) == 0 || tokens[len(tokens)-1].kind != '*' {
				tokens = append(tokens, globToken{kind: '*'})
			}
		case '?':
			tokens = append(tokens, globToken{kind: '?'})
		case '[':
			end := i
			if end < len(characters) && characters[end] == '!' {
				end++
			}
			if end < len(characters) && characters[end] == ']' {
				end++
			}
			for end < len(characters) && characters[end] != ']' {
				end++
			}
			if end == len(characters) {
				tokens = append(tokens, globToken{kind: '=', literal: '['})
			} else {
				tokens = append(tokens, parseGlobClass(characters[i:end]))
				i = end + 1
			}
		default:
			tokens = append(tokens, globToken{kind: '=', literal: char})
		}
	}
	return tokens
}

func parseGlobClass(characters []rune) globToken {
	// Split on range separators, leaving leading/trailing and intervening
	// literal hyphens inside chunks. Remove reversed ranges before interpreting
	// a leading !, exactly as fnmatchcase does (a removal can expose that !).
	var chunks [][]rune
	start, search := 0, 1
	if len(characters) > 0 && characters[0] == '!' {
		search = 2
	}
	for {
		dash := -1
		for i := search; i < len(characters); i++ {
			if characters[i] == '-' {
				dash = i
				break
			}
		}
		if dash < 0 {
			break
		}
		chunks = append(chunks, append([]rune(nil), characters[start:dash]...))
		start, search = dash+1, dash+3
	}
	if start < len(characters) {
		chunks = append(chunks, append([]rune(nil), characters[start:]...))
	} else if len(chunks) > 0 {
		chunks[len(chunks)-1] = append(chunks[len(chunks)-1], '-')
	}
	for i := len(chunks) - 1; i > 0; i-- {
		left, right := chunks[i-1], chunks[i]
		if len(left) > 0 && len(right) > 0 && left[len(left)-1] > right[0] {
			merged := append([]rune(nil), left[:len(left)-1]...)
			chunks[i-1] = append(merged, right[1:]...)
			chunks = append(chunks[:i], chunks[i+1:]...)
		}
	}
	token := globToken{kind: '['}
	if len(chunks) > 0 && len(chunks[0]) > 0 && chunks[0][0] == '!' {
		token.negated = true
		chunks[0] = chunks[0][1:]
	}
	for i, chunk := range chunks {
		first, last := 0, len(chunk)
		if i > 0 {
			first++
		}
		if i+1 < len(chunks) {
			last--
		}
		for j := first; j < last; j++ {
			token.ranges = append(token.ranges, globRuneRange{chunk[j], chunk[j]})
		}
		if i+1 < len(chunks) && len(chunk) > 0 && len(chunks[i+1]) > 0 {
			token.ranges = append(token.ranges, globRuneRange{chunk[len(chunk)-1], chunks[i+1][0]})
		}
	}
	return token
}

func (token globToken) matches(char rune) bool {
	switch token.kind {
	case '=':
		return token.literal == char
	case '?':
		return true
	case '[':
		contained := false
		for _, interval := range token.ranges {
			if interval.low <= char && char <= interval.high {
				contained = true
				break
			}
		}
		return contained != token.negated
	}
	return false
}

func matchGlobSegment(tokens []globToken, name string) bool {
	characters := []rune(name)
	star := false
	for _, token := range tokens {
		if token.kind == '*' {
			star = true
			break
		}
	}
	if !star {
		if len(tokens) != len(characters) {
			return false
		}
		for i, token := range tokens {
			if !token.matches(characters[i]) {
				return false
			}
		}
		return true
	}
	previous := make([]bool, len(characters)+1)
	previous[0] = true
	for _, token := range tokens {
		next := make([]bool, len(previous))
		if token.kind == '*' {
			for i := range next {
				next[i] = previous[i] || i > 0 && next[i-1]
			}
		} else {
			for i := 1; i < len(next); i++ {
				next[i] = previous[i-1] && token.matches(characters[i-1])
			}
		}
		previous = next
	}
	return previous[len(characters)]
}
